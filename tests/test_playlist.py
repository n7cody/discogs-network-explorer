from discogs_network_explorer.playlist import mix_catalogs


def rel(rid, year, videos=0, tracks=0):
    return {"release_id": rid, "year": year, "videos": [{}] * videos, "tracklist": [{}] * tracks}


def ids(releases):
    return [r["release_id"] for r in releases]


def test_takes_newest_release_from_each_input_in_turn():
    a = [rel("a1", 2020, 1), rel("a2", 2024, 1), rel("a3", None, 1)]
    b = [rel("b1", 2023, 1)]
    c = [rel("c1", 2010, 1), rel("c2", 2011, 1)]
    assert ids(mix_catalogs([a, b, c])) == ["a2", "b1", "c2", "a1", "c1", "a3"]


def test_stops_before_the_release_that_would_pass_max_items():
    # Each release counts its videos, or its tracklist entries when it has none
    a = [rel("a1", 2024, videos=2, tracks=9), rel("a2", 2023, tracks=3)]
    b = [rel("b1", 2024, tracks=4), rel("b2", 2023, videos=1)]
    assert ids(mix_catalogs([a, b], max_items=6)) == ["a1", "b1"]
    assert ids(mix_catalogs([a, b], max_items=9)) == ["a1", "b1", "a2"]
    assert len(mix_catalogs([a, b])) == 4


def test_fill_limit_holds_youtube_to_400_and_apple_to_9999():
    from discogs_network_explorer.playlist import fill_limit
    assert fill_limit("YouTube") == 400
    assert fill_limit("Apple Music") == 9999
    assert fill_limit("Apple+YT") == 9999
