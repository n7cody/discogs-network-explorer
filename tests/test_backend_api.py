import pytest

from discogs_network_explorer import backend

ARTIST_RELEASES = {
    "pagination": {"page": 1, "pages": 1},
    "releases": [
        {"id": 11, "type": "release", "title": "Plain", "artist": "A", "role": "Main", "year": 2020},
        {"id": 900, "type": "master", "main_release": 22, "title": "Mastered", "artist": "A",
         "role": "Main", "year": 2021},
        {"id": 901, "type": "master", "title": "No main release", "artist": "A", "role": "Main"},
    ],
}


def _release(rid):
    return {"labels": [{"id": 5, "name": "Lab"}], "year": 2020, "videos": [], "tracklist": [],
            "genres": [], "styles": [], "country": "UK"}


@pytest.fixture
def requested(monkeypatch):
    urls = []

    def fake_get(url, params=None):
        urls.append(url)
        if url.endswith("/artists/1/releases"):
            return ARTIST_RELEASES
        if "/releases/" in url:
            return _release(url.rsplit("/", 1)[1])
        raise AssertionError(url)

    monkeypatch.setattr(backend, "_safe_get", fake_get)
    return urls


def _release_lookups(urls):
    return sorted(u.rsplit("/", 1)[1] for u in urls if "/releases/" in u)


def test_label_rows_follow_master_items_to_their_main_release(requested):
    rows = backend.get_master_label_rows(["1"])
    assert _release_lookups(requested) == ["11", "22"]
    assert sorted(r["release_id"] for r in rows) == ["11", "22"]


def test_artist_catalog_follows_master_items_to_their_main_release(requested):
    rows = backend.get_catalog_videos("artist", "1")
    assert _release_lookups(requested) == ["11", "22"]
    assert sorted(r["release_id"] for r in rows) == ["11", "22"]
