import shutil

import duckdb
import pytest

from discogs_network_explorer import local_db

SEED = "99137"  # Smoke City Music

# Seed pool for 99137 on the fixture, in discovery order
SEED_POOL = ["137519", "479460", "479461", "787103", "959258", "1187208",
             "3184", "819406", "1069536",
             "787104", "787105",
             "456898", "712683", "976467",
             "293378", "582650", "661000",
             "241887", "428437", "457900",
             "1337924", "1499243"]


@pytest.fixture(scope="module", autouse=True)
def db(snapshot):
    local_db.use_snapshot(snapshot)
    yield
    local_db.use_snapshot(None)


def test_seed_pool_in_discovery_order():
    assert local_db.get_master_artist_list([SEED]) == SEED_POOL


def test_seed_window_limits_seed_releases():
    assert local_db.get_master_artist_list([SEED], min_year=2009) == [
        "293378", "479460", "479461", "582650", "661000",
        "241887", "428437", "457900", "787103", "787104", "787105",
        "1337924", "1499243"]


def test_track_artists_count_only_on_various_artists_releases():
    # 787103 is only a Track Artist on 1345585 (not VA); it enters at 1409908 (Main)
    pool = local_db.get_master_artist_list([SEED], min_year=2008, max_year=2008)
    assert pool[:6] == ["3184", "479460", "479461", "819406", "1069536", "787103"]
    # Release 3 is a DJ mix by one Main Artist with 15 Track Artists and 3 remixers
    assert local_db.get_master_artist_list(["6"]) == ["3", "8", "14", "23"]


def test_seed_window_keeps_releases_without_year():
    assert local_db.get_master_artist_list(["184"], min_year=2000, max_year=2001) == ["515", "617", "14332"]


def test_originals_only_drops_non_main_versions():
    assert local_db.get_master_artist_list(["836"]) == ["3225"]
    assert local_db.get_master_artist_list(["836"], originals_only=True) == []


# Artists -> label rows

ROW_KEYS = {"artist_id", "artist_name", "release_id", "release_title", "role", "format_names",
            "format_descriptions", "genres", "styles", "country", "year", "label_id", "label_name"}

# Pool artists per label for the seed pool on the fixture
POOL_ARTISTS_PER_LABEL = {"99137": 22, "44095": 3, "76071": 3, "17379": 3, "24083": 2,
                          "78409": 2, "14057": 2, "84751": 1, "9659": 1, "9768": 1, "97519": 1}


def pool_artists_per_label(rows):
    per_label = {}
    for r in rows:
        per_label.setdefault(r["label_id"], set()).add(r["artist_id"])
    return {k: len(v) for k, v in per_label.items()}


@pytest.fixture(scope="module")
def label_rows():
    return local_db.get_master_label_rows(SEED_POOL)


def test_label_rows_pool_artists_per_label(label_rows):
    assert len(label_rows) == 68
    assert all(set(r) == ROW_KEYS for r in label_rows)
    assert pool_artists_per_label(label_rows) == POOL_ARTISTS_PER_LABEL


def test_label_row_fields():
    rows = local_db.get_master_label_rows(["712683"])
    row = next(r for r in rows if r["release_id"] == "1568748")
    assert row == {
        "artist_id": "712683", "artist_name": "Homero Espinosa", "release_id": "1568748",
        "release_title": "YBDSF Studio Sessions EP", "role": "Main", "format_names": "Vinyl",
        "format_descriptions": '12", EP, White Label', "genres": "Electronic",
        "styles": "Deep House", "country": "UK", "year": 2008, "label_id": "99137",
        "label_name": "Smoke City Music"}


def test_label_rows_ignore_caps_and_years(label_rows):
    rows = local_db.get_master_label_rows(SEED_POOL, max_releases_allowed=1, max_releases_per_label=1,
                                          min_year=2009, max_year=2009, seed_label_ids=[SEED],
                                          max_global_releases=1)
    assert rows == label_rows


def test_label_rows_one_per_artist_release_label():
    rows = local_db.get_master_label_rows(["3225"])  # 50606 lists label 836 twice (two catnos)
    assert sorted((r["release_id"], r["label_id"]) for r in rows) == [
        ("116236", "93"), ("116925", "93"), ("1744568", "836"), ("50606", "836")]


def test_label_rows_originals_only():
    rows = local_db.get_master_label_rows(["3225"], originals_only=True)
    assert [r["release_id"] for r in rows] == ["116925"]


def test_label_rows_exclude_not_on_label():
    assert local_db.get_master_label_rows(["222372"]) == []  # only on 7306, Not On Label


def test_label_rows_artist_missing_from_artists_table():
    rows = local_db.get_master_label_rows(["355"])
    assert [r["artist_name"] for r in rows] == ["artist_355"]


# Label and artist lookups

def test_label_stats():
    assert local_db.get_label_stats([SEED, "14057", "999999999"]) == [
        {"label_id": "14057", "label_name": "Yerba Buena Discos", "catalog_size": 3,
         "earliest_year": 2004, "latest_year": 2007, "n_artists": 4},
        {"label_id": "99137", "label_name": "Smoke City Music", "catalog_size": 9,
         "earliest_year": 2007, "latest_year": 2009, "n_artists": 22},
    ]


def test_label_missing_from_labels_table_named_from_releases(snapshot, tmp_path):
    # 155 labels in the 20261001 database appear on releases but not in the labels Dump
    copy = tmp_path / "snap.duckdb"
    shutil.copy(snapshot, copy)
    with duckdb.connect(str(copy)) as con:
        con.execute("DELETE FROM labels WHERE label_id = 14057")
    local_db.use_snapshot(copy)
    try:
        assert local_db.get_label_stats(["14057"])[0]["label_name"] == "Yerba Buena Discos"
        assert local_db.get_label_name("14057") == "Yerba Buena Discos"
    finally:
        local_db.use_snapshot(snapshot)


def test_label_without_years():
    assert local_db.get_label_earliest_year("1818") is None
    assert local_db.get_label_latest_year("1818") is None


def test_single_label_lookups():
    assert local_db.get_label_release_count(SEED) == 9
    assert local_db.get_label_earliest_year(SEED) == 2007
    assert local_db.get_label_latest_year(SEED) == 2009
    assert local_db.get_label_name(SEED) == "Smoke City Music"


def test_unknown_label_falls_back_like_api():
    assert local_db.get_label_release_count("999999999") == 0
    assert local_db.get_label_earliest_year("999999999") is None
    assert local_db.get_label_latest_year("999999999") is None
    assert local_db.get_label_name("999999999") == "999999999"


def test_artist_stats():
    assert local_db.get_artist_stats(["712683", "3225", "999999999"]) == [
        {"artist_id": "3225", "earliest_year": 2002, "latest_year": 2002, "n_releases": 4},
        {"artist_id": "712683", "earliest_year": 2004, "latest_year": 2008, "n_releases": 4},
    ]


def test_artist_name():
    assert local_db.get_artist_name("712683") == "Homero Espinosa"
    assert local_db.get_artist_name("999999999") == "999999999"


# dnx catalogs

# Seed label catalog newest first, with playlist items per release (videos, else tracks)
SEED_CATALOG = [("1834508", 3), ("1752850", 2), ("1658191", 2), ("1568748", 3), ("1559757", 2),
                ("1524016", 2), ("1409908", 4), ("1345585", 4), ("1129429", 3)]


def catalog_ids(**kwargs):
    return [r["release_id"] for r in local_db.get_catalog_videos(**kwargs)]


def test_catalog_newest_first():
    assert catalog_ids(entity_type="label", entity_id=SEED) == [rid for rid, _ in SEED_CATALOG]


def test_catalog_adds_whole_releases_up_to_max_tracks():
    ids = [rid for rid, _ in SEED_CATALOG]
    assert catalog_ids(entity_type="label", entity_id=SEED, max_tracks=10) == ids[:4]
    assert catalog_ids(entity_type="label", entity_id=SEED, max_tracks=11) == ids[:4]
    assert catalog_ids(entity_type="label", entity_id=SEED, max_tracks=12) == ids[:5]
    assert catalog_ids(entity_type="label", entity_id=SEED, max_tracks=2) == []


def test_catalog_ignores_max_releases():
    assert len(catalog_ids(entity_type="label", entity_id=SEED, max_releases=1)) == 9


def test_catalog_year_window():
    assert catalog_ids(entity_type="label", entity_id=SEED, min_year=2009) == ["1834508", "1752850", "1658191"]
    assert len(catalog_ids(entity_type="label", entity_id=SEED, max_year=2008)) == 6
    assert catalog_ids(entity_type="label", entity_id="184", min_year=2000, max_year=2001) == ["367"]


def test_catalog_release_shape():
    rel = local_db.get_catalog_videos("label", SEED)[-1]
    assert rel == {
        "release_id": "1129429",
        "release_title": "Early Smoke E.P",
        "artist_name": "Slum Science, The Hoodfellas",
        "year": 2007,
        "videos": [
            {"url": "https://www.youtube.com/watch?v=Pg6BOAmdmdM", "title": "Slum Science - Down Time"},
            {"url": "https://www.youtube.com/watch?v=jUCkadauezM", "title": "The Hoodfellas -  3 Way"},
            {"url": "https://www.youtube.com/watch?v=75Ars6TZzBM",
             "title": "The Hoodfellas  - 3 Way (Danny White Redid)"},
        ],
        "tracklist": [
            {"position": "A1", "title": "Down Time", "artist": "Slum Science"},
            {"position": "B1", "title": "3 Way", "artist": "The Hoodfellas"},
            {"position": "B2", "title": "3 Way (Danny White Redid)", "artist": "The Hoodfellas"},
        ],
        "label_name": "Smoke City Music",
    }


def test_catalog_tracklist_artists():
    by_id = {r["release_id"]: r for r in local_db.get_catalog_videos("label", SEED)}
    various = by_id["1524016"]
    assert various["artist_name"] == "Various"
    assert various["tracklist"][0]["artist"] == "Non Believers, Astral T"
    # no track artists: fall back to the release's Main Artists
    assert {t["artist"] for t in by_id["1834508"]["tracklist"]} == {"Quike"}
    assert by_id["1345585"]["videos"] == []


def test_artist_catalog():
    rels = local_db.get_catalog_videos("artist", "3225")
    assert [r["release_id"] for r in rels] == ["1744568", "116925", "116236", "50606"]
    assert {r["label_name"] for r in rels} == {""}
    assert catalog_ids(entity_type="artist", entity_id="3225", originals_only=True) == ["116925"]


# API-only functions and the connection

def test_api_only_functions():
    from discogs_network_explorer import backend

    assert local_db.build_label_to_artists is backend.build_label_to_artists
    assert local_db.enable_http_cache() is None
    assert local_db.clear_http_cache("discogs_cache") is None


def test_swapped_snapshot_is_picked_up(snapshot, tmp_path, monkeypatch):
    old = tmp_path / "discogs_20261001.duckdb"
    shutil.copy(snapshot, old)
    monkeypatch.setenv("DNE_DB_PATH", str(tmp_path))
    local_db.use_snapshot(None)
    try:
        assert local_db.get_label_name(SEED) == "Smoke City Music"
        # what dne-update does: build the next database, then delete the old one
        new = tmp_path / "discogs_20261101.duckdb"
        shutil.copy(snapshot, new)
        with duckdb.connect(str(new)) as con:
            con.execute("UPDATE labels SET name = 'Smoke City Music (renamed)' WHERE label_id = 99137")
        old.unlink()
        assert local_db.get_label_name(SEED) == "Smoke City Music (renamed)"
    finally:
        local_db.use_snapshot(snapshot)


def test_query_cursors_use_snapshot_settings():
    cur = local_db._cursor()
    try:
        for name, value in local_db.SETTINGS.items():
            assert cur.execute(f"SELECT current_setting('{name}')").fetchone()[0] == value
    finally:
        cur.close()


def test_snapshot_meta_readable_while_connected(snapshot):
    from discogs_network_explorer.ingest.build import read_meta

    local_db.get_label_name(SEED)
    assert read_meta(snapshot)["dump_date"] == "20261001"


def test_missing_snapshot(tmp_path, monkeypatch, snapshot):
    monkeypatch.setenv("DNE_DB_PATH", str(tmp_path))
    local_db.use_snapshot(None)
    try:
        with pytest.raises(local_db.SnapshotMissing):
            local_db.get_label_name(SEED)
    finally:
        local_db.use_snapshot(snapshot)
