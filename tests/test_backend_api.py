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


LABEL_RELEASES = {
    "pagination": {"page": 1, "pages": 1},
    "releases": [{"id": 60, "title": "Main", "artist": "B", "year": 2020},
                 {"id": 61, "title": "Repress", "artist": "B", "year": 2020}],
}

RELEASE_50 = {
    "labels": [{"id": 5, "name": "Lab"}], "year": 2020, "videos": [], "genres": [], "styles": [],
    "country": "UK", "master_id": 77, "artists": [{"id": 3, "name": "Main Artist"}],
    "formats": [{"name": "Vinyl", "descriptions": ['12"', "EP"]}],
    "tracklist": [
        {"type_": "heading", "position": "", "title": "Side A", "duration": ""},
        {"type_": "index", "position": "", "title": "Suite", "duration": "",
         "artists": [{"id": 4, "name": "Index Artist"}],
         "sub_tracks": [
             {"type_": "track", "position": "1a", "title": "a", "duration": "3:00",
              "artists": [{"id": 5, "name": "Sub Artist"}]},
             {"type_": "track", "position": "1b", "title": "b", "duration": ""}]},
        {"type_": "track", "position": "2", "title": "C", "duration": "4:00"},
    ],
}


def _release(rid):
    if rid == "50":
        return RELEASE_50
    rel = {"labels": [{"id": 5, "name": "Lab"}], "year": 2020, "videos": [], "tracklist": [],
           "genres": [], "styles": [], "country": "UK"}
    if rid in ("60", "61"):
        rel.update(master_id=77, artists=[{"id": int(rid) * 10, "name": f"Artist {rid}"}])
    return rel


@pytest.fixture
def requested(monkeypatch):
    urls = []
    monkeypatch.setattr(backend, "_MASTER_MAIN", {})

    def fake_get(url, params=None):
        urls.append(url)
        if url.endswith("/artists/1/releases"):
            return ARTIST_RELEASES
        if url.endswith("/labels/5/releases"):
            return LABEL_RELEASES
        if url.endswith("/masters/77"):
            return {"id": 77, "main_release": 60}
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


def test_artist_master_items_skip_master_lookup(requested):
    backend.get_master_label_rows(["1"])
    backend.get_catalog_videos("artist", "1")
    assert not [u for u in requested if "/masters/" in u]


def test_release_details_playable_tracklist_and_fields(requested):
    d = backend.get_release_details("50")
    assert d["master_id"] == "77"
    assert d["format_descriptions"] == ['12"', "EP"]
    assert [t["title"] for t in d["tracklist"]] == ["a", "b", "C"]
    assert all("duration" in t for t in d["tracklist"])
    assert [t["artist"] for t in d["tracklist"]] == ["Sub Artist", "Index Artist", "Main Artist"]


def test_label_catalog_applies_master_rule(requested):
    rows = backend.get_catalog_videos("label", "5")
    assert [r["release_id"] for r in rows] == ["60"]
    assert rows[0]["master_id"] == "77"
    assert rows[0]["format_descriptions"] == []
    assert sum(u.endswith("/masters/77") for u in requested) == 1


def test_seed_pool_applies_master_rule(requested):
    assert backend.get_master_artist_list(["5"]) == ["600"]
