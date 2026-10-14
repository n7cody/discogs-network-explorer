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


def test_dedupe_keeps_same_release_once_in_first_catalog():
    from discogs_network_explorer.playlist import dedupe_releases
    x = rel("x", 2024, 1)
    out = dedupe_releases([[rel("a", 2020, 1), x], [dict(x), rel("b", 2021, 1)]])
    assert [ids(c) for c in out] == [["a", "x"], ["b"]]
    assert out[0][1]["merged_release_ids"] == []


def test_dedupe_master_prefers_release_with_videos():
    from discogs_network_explorer.playlist import dedupe_releases
    a = {**rel("101", 2020, 0, 5), "master_id": "2950303"}
    b = {**rel("102", 2020, 1), "master_id": "2950303"}
    out = dedupe_releases([[a, b]])
    assert ids(out[0]) == ["102"]
    assert out[0][0]["merged_release_ids"] == ["101"]


def test_dedupe_masterless_releases_all_kept():
    from discogs_network_explorer.playlist import dedupe_releases
    a = {**rel("1", 2020, 1), "master_id": None}
    b = {**rel("2", 2020, 1), "master_id": None}
    out = dedupe_releases([[a, b]])
    assert ids(out[0]) == ["1", "2"]
    assert all(r["merged_release_ids"] == [] for r in out[0])


def test_dedupe_master_tie_break():
    from discogs_network_explorer.playlist import dedupe_releases

    def m(rid, year, tracks):
        return {**rel(rid, year, 0, tracks), "master_id": "9"}

    assert ids(dedupe_releases([[m("5", 2020, 2), m("6", 2020, 3)]])[0]) == ["6"]
    assert ids(dedupe_releases([[m("5", 2019, 3), m("6", 2020, 3)]])[0]) == ["6"]
    assert ids(dedupe_releases([[m("6", 2020, 3), m("5", 2020, 3)]])[0]) == ["5"]


def test_mix_catalogs_duplicates_do_not_count_toward_limit():
    x = rel("x", 2024, 3)
    b2 = rel("b2", 2023, 1)
    assert ids(mix_catalogs([[x], [dict(x), b2]], max_items=4)) == ["x", "b2"]
