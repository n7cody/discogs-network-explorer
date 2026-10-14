import shutil
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
    assert any("dne-update" in c.value for c in at.sidebar.caption)
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
