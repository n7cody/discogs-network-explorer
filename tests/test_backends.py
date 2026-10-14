import inspect

import pytest

from discogs_network_explorer import backend, local_db
from discogs_network_explorer.backends import INTERFACE, get_backend, snapshot_available

# What app.py imports from backend today
APP_IMPORTS = {
    "build_label_to_artists", "clear_http_cache", "enable_http_cache", "get_artist_name",
    "get_catalog_videos", "get_label_earliest_year", "get_label_latest_year", "get_label_name",
    "get_label_release_count", "get_master_artist_list", "get_master_label_rows",
}


def test_get_backend():
    assert get_backend("snapshot") is local_db
    assert get_backend("api") is backend
    with pytest.raises(ValueError):
        get_backend("excel")


def test_interface_covers_app_imports():
    assert APP_IMPORTS <= set(INTERFACE)


@pytest.mark.parametrize("name", sorted(APP_IMPORTS))
def test_snapshot_accepts_every_api_argument(name):
    """local_db takes the API's parameters in the same order, plus optional extras."""
    api = list(inspect.signature(getattr(backend, name)).parameters.values())
    snap = list(inspect.signature(getattr(local_db, name)).parameters.values())
    assert [p.name for p in snap[:len(api)]] == [p.name for p in api]
    assert all(p.default is not inspect.Parameter.empty for p in snap[len(api):])


def test_snapshot_available(tmp_path, monkeypatch):
    monkeypatch.setenv("DNE_DB_PATH", str(tmp_path))
    assert not snapshot_available()
    (tmp_path / "discogs_20261001.duckdb").touch()
    assert snapshot_available()
