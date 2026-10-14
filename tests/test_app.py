import io
import shutil
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from discogs_network_explorer import local_db
from discogs_network_explorer.filters import filter_by_format

APP = str(Path(__file__).parent.parent / "src" / "discogs_network_explorer" / "app.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated launch directory: an empty .env, no token, an empty database directory."""
    (tmp_path / ".env").write_text("")
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
    monkeypatch.setenv("DNE_DB_PATH", str(db_dir))
    local_db.use_snapshot(None)
    yield db_dir
    local_db.reconnect()


def test_without_database_app_uses_api_and_asks_for_token(env):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert len(at.sidebar.radio) == 0
    assert any("dne-db-update" in c.value for c in at.sidebar.caption)
    assert any("access token" in w.value for w in at.warning)


def test_with_database_app_defaults_to_it_without_token(env, snapshot):
    shutil.copy(snapshot, env / "discogs_20261001.duckdb")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert at.sidebar.radio[0].value == "snapshot"
    assert "2026-10-01" in str(at.sidebar.radio[0].proto)
    assert not at.warning

    at.sidebar.text_area[0].set_value("99137")
    at.sidebar.slider[0].set_value((1900, at.sidebar.slider[0].max))
    next(b for b in at.sidebar.button if b.label == "Run Analysis").click().run()
    assert not at.exception
    df_raw = at.session_state["df_raw"]
    assert "format" in df_raw.columns and "format_names" not in df_raw.columns
    assert at.session_state["source_used"] == "snapshot"
    assert at.session_state["label_counts"]["99137"] == 9


def test_switching_to_api_shows_token_prompt(env, snapshot):
    shutil.copy(snapshot, env / "discogs_20261001.duckdb")
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.sidebar.radio[0].set_value("api").run()
    assert not at.exception
    assert any("access token" in w.value for w in at.warning)


def test_format_filter_matches_any_listed_format():
    df = pd.DataFrame({"format": ["CD, Vinyl", "Vinyl", "File", None, "cd"]})
    assert filter_by_format(df, {"CD"})["format"].tolist() == ["CD, Vinyl", "cd"]
    assert filter_by_format(df, {"vinyl", "File"})["format"].tolist() == ["CD, Vinyl", "Vinyl", "File"]
    assert filter_by_format(df, set()) is df


# Helpers

def _by_label(widgets, label):
    return next(w for w in widgets if w.label == label)


def _labels(widgets):
    return [w.label for w in widgets]


_media: list = []


@pytest.fixture(autouse=True)
def media(monkeypatch):
    """Keep AppTest's in-memory media store so download button data can be read."""
    from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage
    from streamlit.testing.v1 import app_test

    class Kept(MemoryMediaFileStorage):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            _media.append(self)

    monkeypatch.setattr(app_test, "MemoryMediaFileStorage", Kept)


def _zip_csvs(at, label="Download results (CSV)"):
    """The CSV files inside a download button's ZIP, as DataFrames by file name."""
    url = next(d.proto.url for d in at.get("download_button") if d.proto.label == label)
    data = _media[-1].get_file(url.rsplit("/", 1)[1]).content
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: pd.read_csv(zf.open(n), dtype={"label_id": str}) for n in zf.namelist() if n.endswith(".csv")}


@pytest.fixture
def local_app(env, snapshot):
    shutil.copy(snapshot, env / "discogs_20261001.duckdb")
    return AppTest.from_file(APP, default_timeout=30).run()


def _run(at, seeds, **checks):
    at.sidebar.text_area[0].set_value(seeds)
    at.sidebar.slider[0].set_value((1900, at.sidebar.slider[0].max))
    for label, value in checks.items():
        _by_label(at.sidebar.checkbox, label).set_value(value)
    return _by_label(at.sidebar.button, "Run Analysis").click().run()


@pytest.fixture
def api_app(env, monkeypatch):
    monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
    return AppTest.from_file(APP, default_timeout=30).run()


# Master rule (always on)

def test_no_original_releases_toggle(local_app):
    assert "Original Releases only" not in _labels(local_app.sidebar.checkbox)


def test_master_rule_always_applies(local_app):
    # Label 836's only release is a non-main version of its master
    at = _run(local_app, "836")
    assert not at.exception
    assert any("No artists" in e.value for e in at.error)
    assert "originals_used" not in at.session_state


def test_outdated_database_falls_back_to_api(env, snapshot):
    import duckdb

    db = env / "discogs_20261001.duckdb"
    shutil.copy(snapshot, db)
    with duckdb.connect(str(db)) as con:
        con.execute("UPDATE snapshot_meta SET schema_version = 1")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert len(at.sidebar.radio) == 0
    assert any("dne-db-update" in c.value and "older" in c.value for c in at.sidebar.caption)


# Label overlap

MIN_CONC = "Min label overlap (%)"


def test_label_overlap_column_in_discovered_labels(local_app):
    at = _run(local_app, "99137")
    assert not at.exception
    labels = _zip_csvs(at)["discovered_labels.csv"].set_index("label_id")
    # 99137: all 22 of its artists are in the pool; 14057: 2 of its 4
    assert labels.loc["99137", "label_overlap_pct"] == 100.0
    assert labels.loc["14057", "label_overlap_pct"] == 50.0


def test_label_overlap_filter_off_by_default_and_drops_labels_below(local_app):
    at = _run(local_app, "99137")
    assert _by_label(at.sidebar.number_input, MIN_CONC).value == 0
    assert "14057" in set(at.session_state["df_raw"]["label_id"])
    shown = lambda: set(at.dataframe[1].value["label_id"].astype(str))
    assert "14057" in shown()

    _by_label(at.sidebar.number_input, MIN_CONC).set_value(60.0).run()
    assert not at.exception
    assert "14057" not in shown()
    assert "99137" in shown()


def test_export_follows_filters_and_ranks_by_weighted_overlap(local_app):
    at = _run(local_app, "99137")
    labels = _zip_csvs(at)["discovered_labels.csv"]
    assert "14057" in set(labels["label_id"])
    assert labels["weighted_overlap_pct"].is_monotonic_decreasing
    assert labels.set_index("label_id").loc["99137", "weighted_overlap_pct"] == 100.0
    assert {"inputs_reached", "label_overlap_pct"} <= set(labels.columns)
    assert "overlap_ratio" not in labels.columns

    _by_label(at.sidebar.number_input, MIN_CONC).set_value(60.0).run()
    labels = _zip_csvs(at)["discovered_labels.csv"]
    assert "14057" not in set(labels["label_id"])
    assert "99137" in set(labels["label_id"])


def test_dj_mixes_have_no_toggle(local_app):
    at = _run(local_app, "99137")
    assert "Exclude DJ mixes" not in {c.label for c in at.sidebar.checkbox}
    assert "exclude_dj_mixes" not in _params(at)


def test_graph_names_toggle_swaps_versions(local_app):
    at = _run(local_app, "99137")
    toggle = next(t for t in at.toggle if t.label == "Show names")
    assert toggle.value is True
    with_names = at.session_state["graph_png"]
    toggle.set_value(False).run()
    assert not at.exception
    assert at.session_state["graph_png_no_names"] != with_names


# Catalog-size cap

CAP_ON = "Cap catalog size"
CAP = "Max catalog size (releases)"


def test_catalog_cap_off_by_default(local_app):
    at = _run(local_app, "99137")
    assert _by_label(at.sidebar.checkbox, CAP_ON).value is False
    assert CAP not in _labels(at.sidebar.number_input)
    assert "excluded_labels.csv" not in _zip_csvs(at)
    assert not any(e.label.startswith("Excluded labels") for e in at.expander)


def test_catalog_cap_excludes_large_labels_and_records_them(local_app):
    at = _run(local_app, "99137")
    _by_label(at.sidebar.checkbox, CAP_ON).check().run()
    assert _by_label(at.sidebar.number_input, CAP).value == 400
    # 14057 has 3 releases; seed label 99137 (9 releases) is never excluded
    _by_label(at.sidebar.number_input, CAP).set_value(2).run()
    assert not at.exception
    assert "14057" not in set(at.dataframe[1].value["label_id"].astype(str))
    assert any(e.label.startswith("Excluded labels") for e in at.expander)
    for zip_label in ("Download results (CSV)", "Download report (ZIP)"):
        excluded = _zip_csvs(at, zip_label)["excluded_labels.csv"].set_index("label_id")
        assert list(excluded.columns) == ["label_name", "catalog_size", "n_pool_artists"]
        assert excluded.loc["14057"].tolist() == ["Yerba Buena Discos", 3, 2]
        assert "99137" not in excluded.index


# Release Activity Window over each artist's full history

def test_artist_activity_uses_full_history_in_local_mode(local_app):
    at = local_app
    at.sidebar.selectbox[0].set_value("Artists Only").run()
    at.sidebar.text_area[1].set_value("712683,3225")
    _by_label(at.sidebar.number_input, "Min shared artists from input list").set_value(1)
    _by_label(at.sidebar.checkbox, "Release Activity Window").check()
    at = _run(at, "")
    _by_label(at.sidebar.slider, "Activity window year range").set_value((2007, 2008))
    _by_label(at.sidebar.slider, "Min releases within activity window").set_value(3)
    # The Results Window keeps 712683's one original 2008 release; its full history has three
    _by_label(at.sidebar.slider, "Min releases per label").set_value(1)
    _by_label(at.sidebar.number_input, "Year from").set_value(2008).run()
    assert not at.exception
    assert set(at.dataframe[0].value["artist_id"].astype(str)) == {"712683"}


# Search parameters recorded in the export

def _params(at):
    info = _zip_csvs(at)["input_info.csv"]
    info = info[info["section"] == "Search Parameters"]
    return dict(zip(info["field"], info["value"].astype(str)))


def test_export_records_local_settings(local_app):
    at = _run(local_app, "99137")
    params = _params(at)
    assert params["release_caps"] == "no caps"
    assert "originals_only" not in params
    assert params["min_label_overlap_pct"] == "off"
    assert params["catalog_size_cap"] == "off"
    assert "max_releases_per_artist" not in params

    _by_label(at.sidebar.number_input, MIN_CONC).set_value(5.0).run()
    _by_label(at.sidebar.checkbox, CAP_ON).check().run()
    params = _params(at)
    assert params["min_label_overlap_pct"] == "5.0"
    assert params["catalog_size_cap"] == "400"


# API mode keeps its own controls

@pytest.fixture
def stub_api(monkeypatch, snapshot):
    """The API backend answering from the fixture database, without HTTP."""
    from discogs_network_explorer import backend

    local_db.use_snapshot(snapshot)
    rows = [{**{k: v for k, v in r.items() if k not in ("format_names", "format_descriptions")},
             "format": r["format_names"]}
            for r in local_db.get_master_label_rows(local_db.get_master_artist_list(["99137"]))]
    for name, fn in {
        "get_master_artist_list": lambda *a, **kw: local_db.get_master_artist_list(["99137"]),
        "get_master_label_rows": lambda *a, **kw: rows,
        "get_label_earliest_year": local_db.get_label_earliest_year,
        "get_label_latest_year": local_db.get_label_latest_year,
        "get_label_release_count": local_db.get_label_release_count,
        "get_label_name": local_db.get_label_name,
        "get_artist_name": local_db.get_artist_name,
    }.items():
        monkeypatch.setattr(backend, name, fn)


def test_api_mode_shows_no_local_only_controls(api_app, stub_api):
    at = _run(api_app, "99137")
    assert not at.exception
    assert at.session_state["source_used"] == "api"
    assert MIN_CONC not in _labels(at.sidebar.number_input)
    assert CAP_ON not in _labels(at.sidebar.checkbox)
    assert "label_overlap_pct" not in at.dataframe[1].value.columns
    csvs = _zip_csvs(at)
    assert "label_overlap_pct" not in csvs["discovered_labels.csv"].columns
    assert "excluded_labels.csv" not in csvs
    params = _params(at)
    assert params["max_releases_per_artist"] == "40"
    assert "release_caps" not in params


# dnx review table

def test_dnx_review_shows_searched_track(local_app, monkeypatch):
    import re

    from discogs_network_explorer import apple_music
    from discogs_network_explorer.matching import REVIEW_COLUMNS, query_key, review_rows

    queries: list[str] = []

    def fake_search_songs(developer_token, query, storefront="us", limit=5):
        queries.append(query)
        artist, title = query.split(" - ", 1)
        return [{"song_id": "id-" + title, "title": title + " (Dub)", "artist": artist}]

    monkeypatch.setattr(apple_music, "load_config", lambda: {"developer_token": "dev"})
    monkeypatch.setattr(apple_music, "load_user_token", lambda: "user")
    monkeypatch.setattr(apple_music, "search_songs", fake_search_songs)

    at = _run(local_app, "99137")
    assert not at.exception
    at.radio(key="dnx_platform").set_value("Apple Music").run()
    _by_label(at.radio, "Input type").set_value("Label IDs").run()
    _by_label(at.text_input, "Label IDs (comma separated)").set_value("99137")
    _by_label(at.button, "Build Apple Music Playlist").click().run()
    assert not at.exception
    if "dnx_pending" not in at.session_state:
        at.run()
    pending = at.session_state["dnx_pending"]

    assert any(b["discogs_track"] == "Down Time" and b["release"] == "Early Smoke E.P"
               and b["query_kind"] == "track" for b in pending["borderline"])
    assert list(review_rows(pending["borderline"], include=True)[0]) == REVIEW_COLUMNS
    assert not [q for q in queries if re.search(r" \(\d+\)", q)]
    keys = {query_key({"query_kind": "track", "artist": q.split(" - ", 1)[0], "title": q.split(" - ", 1)[1]})
            for q in queries}
    assert len(queries) == len(keys)
