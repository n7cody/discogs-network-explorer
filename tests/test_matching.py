import unicodedata

from discogs_network_explorer.matching import (
    REVIEW_COLUMNS,
    artist_score,
    build_query,
    clean_artist,
    dedupe_matches,
    dedupe_queue,
    fold,
    pick_best,
    playable_tracks,
    release_level_listing,
    review_rows,
    score_match,
    search_items,
    split_title,
    title_score,
    unobtained_rows,
)

NIGHT_DRIVE = "ナイトドライブ"


# fold

def test_fold_nfd_japanese_equals_nfc():
    assert fold(unicodedata.normalize("NFD", NIGHT_DRIVE)) == fold(NIGHT_DRIVE)


def test_fold_latin():
    assert fold("Flørist") == "florist"
    assert fold("Beyoncé") == "beyonce"
    assert fold("Pre​-​extinction") == "pre-extinction"
    assert fold("  Physically Sick 3 ") == "physically sick 3"


# clean_artist

def test_clean_artist():
    assert clean_artist("Suchi (3)") == "Suchi"
    assert clean_artist("Aquarium (8), 外神田deepspace") == "Aquarium, 外神田deepspace"
    assert clean_artist("ANF - Topic") == "ANF"
    assert clean_artist("OrbitalVEVO") == "Orbital"


# artist_score

def test_artist_score_shared_parts():
    assert artist_score("Pacific Spirit", "Pacific Spirit & Patrick Holland") == 1.0
    assert artist_score("外神田deepspace", "Aquarium aka 外神田deepspace") == 1.0
    assert artist_score("Killer DJs", "Killer DJ's, Big Zen & Dust-e-1") == 1.0


def test_artist_score_partial():
    assert artist_score("Robert Lowe (2)", "Mr Lowe") < 0.8
    assert 0.5 <= artist_score("Richard Ryan Wenger", "Richard - Topic") < 0.8


# split_title

def test_split_title_credits_and_versions():
    t = split_title("Day One (feat. Dina Ipavic)")
    assert (t.base, t.versions) == ("day one", frozenset())
    t = split_title("Are You Alive? (feat. Penelope Isles) [Edit]")
    assert (t.base, t.versions) == ("are you alive", frozenset({"edit"}))
    assert split_title("Follow It Up (Dandana Mix)").versions == frozenset({"dandana mix"})


def test_split_title_noise_and_disc_markers():
    assert split_title("3m4-Disc1").base == "3m4"
    t = split_title("A Long Way from Home (Bonus Track)")
    assert (t.base, t.versions) == ("a long way from home", frozenset())
    t = split_title("Song (2012 Remaster)")
    assert (t.base, t.versions) == ("song", frozenset())
    assert split_title("Track - Original Mix").base == "track"
    assert split_title("Giving (Official Video) [HD]").base == "giving"


def test_split_title_mixed():
    t = split_title("2.2 (Mixed)")
    assert (t.base, t.versions, t.mixed) == ("2 2", frozenset(), True)


def test_split_title_artist_prefix():
    assert split_title("SYANIDE - RASH", strip_prefixes=["syanide"]).base == "rash"
    assert split_title("[PR021] Pacific Spirit - Giving", strip_prefixes=["pacific spirit"]).base == "giving"
    assert split_title("Pacific Spirit 'Giving'", strip_prefixes=["pacific spirit"]).base == "giving"


def test_split_title_keeps_subtitles_and_digits():
    assert split_title("3 Way (Danny White Redid)").base == "3 way danny white redid"
    assert split_title("SCN 4").base != split_title("SCN 3").base


# title_score

def test_title_score():
    tm = title_score("3m1", "3m1 (Hyper Eclipse)-Disc2")
    assert (tm.score, tm.versions_match) == (0.9, True)
    assert tuple(title_score("Dvote", "Dvote (Bank Holiday Edit)")) == (1.0, False, False)
    assert title_score("Home", "A Long Way from Home (Bonus Track)").score < 0.4
    tm = title_score("2.2", "2.2 (Mixed)")
    assert (tm.score, tm.versions_match, tm.mixed) == (1.0, True, True)


# score_match

def test_score_match_apple():
    assert score_match("Orbital", "Day One", "Orbital", "Day One (feat. Dina Ipavic)").verdict == "accept"
    assert score_match("Pacific Spirit", "Giving", "Pacific Spirit & Patrick Holland", "Giving").verdict == "accept"
    assert score_match("Exael", "Flowered Knife Shadows", "EXAEL", "Quikgel").verdict == "reject"
    assert score_match("Orbital", "Home", "Orbital", "A Long Way from Home (Bonus Track)").verdict == "reject"
    assert score_match("Flørist", "Dvote", "Florist", "Dvote (Bank Holiday Edit)").verdict == "borderline"
    assert score_match("Wild Planet", "Vocoder (Version One)", "Wild Planet", "Vocoder").verdict == "borderline"


def test_score_match_youtube():
    yt = "yt_search"
    assert score_match("uon", "Door", "don", "uon - door", yt).verdict == "accept"
    assert score_match("Syanide", "RASH", "SYANIDE", "SYANIDE - RASH", yt).verdict == "accept"
    assert score_match("ANF (4)", "Mauna Kea", "ANF - Topic", "Mauna Kea", yt).verdict == "accept"
    assert score_match("Richard Ryan Wenger", "Seven Years", "Richard - Topic", "Seven Years Later",
                       yt).verdict == "borderline"
    assert score_match("Robert Lowe (2)", "Gravitational Pull", "Mr Lowe", "Mario Kart In Real Life",
                       yt).verdict == "reject"


def test_score_match_release_query_never_accepts():
    v = score_match("CFCF", "The Colours Of Life", "CFCF", "The Colours Of Life", query_kind="release")
    assert v.verdict == "borderline"


def test_score_match_mixed_is_not_a_version_mismatch():
    v = score_match("Killer DJs", "2.2", "Killer DJ's", "2.2 (Mixed)")
    assert (v.verdict, v.mixed) == ("accept", True)


def test_score_match_unicode():
    v = score_match("Aquarium (8), 外神田deepspace", NIGHT_DRIVE, "Aquarium aka 外神田deepspace",
                    unicodedata.normalize("NFD", NIGHT_DRIVE))
    assert v.verdict == "accept"


# pick_best

KILLER = {"artist": "Killer DJs", "title": "2.2", "query_kind": "track"}


def song(title, sid=None):
    return {"song_id": sid or title, "title": title, "artist": "Killer DJ's"}


def test_pick_best_prefers_non_mixed():
    best, v = pick_best(KILLER, [song("2.2 (Mixed)"), song("2.2")], "am_search")
    assert best["title"] == "2.2"


def test_pick_best_mixed_when_only_offering():
    best, v = pick_best(KILLER, [song("2.2 (Mixed)")], "am_search")
    assert best["title"] == "2.2 (Mixed)"
    assert v.verdict == "accept"


def test_pick_best_verdict_beats_mixed():
    best, v = pick_best(KILLER, [song("2.2 (Mixed)"), song("2.2 (Dub)")], "am_search")
    assert best["title"] == "2.2 (Mixed)"


def test_pick_best_empty():
    assert pick_best(KILLER, [], "am_search") == (None, None)


# playable_tracks

def test_playable_tracks_drops_headings():
    tl = [{"position": "", "title": "Disc I", "duration": ""}, {"position": "A1", "title": "3m1"},
          {"position": "", "title": "Disc II", "duration": ""}, {"position": "C1", "title": "3m1"}]
    assert [t["title"] for t in playable_tracks(tl)] == ["3m1", "3m1"]


def test_playable_tracks_keeps_unpositioned_with_duration():
    tl = [{"position": "", "title": "Mass Murder", "duration": "5:20"}, {"position": "1", "title": "X"}]
    assert [t["title"] for t in playable_tracks(tl)] == ["Mass Murder", "X"]


def test_playable_tracks_sub_tracks():
    tl = [{"type": "heading", "title": "Side A"},
          {"type": "index", "title": "Suite", "artist": "P",
           "sub_tracks": [{"position": "1a", "title": "a"}, {"position": "1b", "title": "b"}]},
          {"type": "track", "position": "2", "title": "C", "artist": "Q"}]
    out = playable_tracks(tl)
    assert [t["title"] for t in out] == ["a", "b", "C"]
    assert [t["artist"] for t in out] == ["P", "P", "Q"]
    assert set(out[0]) == {"position", "title", "artist", "duration"}


def test_playable_tracks_all_unpositioned_kept():
    tl = [{"position": "", "title": "A"}, {"position": "", "title": "B"}]
    assert [t["title"] for t in playable_tracks(tl)] == ["A", "B"]


# release_level_listing and search_items

def rel(title, tracks, descs, rid="1", artist="CFCF"):
    return {"release_id": rid, "release_title": title, "artist_name": artist, "label_name": "L",
            "format_descriptions": descs,
            "tracklist": [{"position": str(i + 1), "title": t, "artist": artist} for i, t in enumerate(tracks)]}


def test_release_level_listing():
    assert release_level_listing(rel("The Colours Of Life", ["The Colours Of Life"], ["File", "FLAC", "Album"]))
    assert not release_level_listing(rel("Bleak Affirmation", ["Bleak Affirmation"], ['7"', "Single Sided"]))
    assert not release_level_listing(rel("Homeward", ["A", "Homeward", "B", "C"], ["Album"]))
    assert release_level_listing(rel("X", [], []))


def test_search_items_release_level():
    items = search_items(rel("The Colours Of Life", ["The Colours Of Life"], ["Album"]), release_fallback=True)
    assert len(items) == 1
    assert items[0]["query_kind"] == "release"


def test_search_items_empty_tracklist():
    r = rel("Lonely", [], ["EP"])
    assert search_items(r, release_fallback=False) == []
    (item,) = search_items(r, release_fallback=True)
    assert (item["title"], item["query_kind"]) == ("Lonely", "release")


def test_search_items_tracks():
    items = search_items(rel("Two Tracker", ["One", "Two"], ["EP"]), release_fallback=True)
    assert [i["query_kind"] for i in items] == ["track", "track"]
    assert [i["discogs_track"] for i in items] == ["One", "Two"]
    assert {i["release"] for i in items} == {"Two Tracker"}


def test_build_query():
    assert build_query("Suchi (3)", "Lotus (Priori Remix)") == "Suchi - Lotus (Priori Remix)"


# dedupe_queue

def item(seq, artist, title, rid, kind="track"):
    src = {"rid": rid, "release": "R" + rid, "label": "L", "merged_release_ids": []}
    return {"seq": seq, "artist": artist, "title": title, "discogs_track": title, "release": src["release"],
            "label": "L", "rid": rid, "query_kind": kind, "sources": [src]}


def rids(entry):
    return [s["rid"] for s in entry["sources"]]


def test_dedupe_queue_same_release_twice():
    out = dedupe_queue([item(18, "¬ b", "3m1", "R"), item(1, "¬ b", "3m1", "R")])
    assert len(out) == 1
    assert rids(out[0]) == ["R"]


def test_dedupe_queue_across_releases():
    out = dedupe_queue([item(1, "外神田deepspace", "Calm Yellow", "10207203"),
                        item(5, "外神田deepspace", "Calm Yellow", "9085955")])
    assert len(out) == 1
    assert rids(out[0]) == ["10207203", "9085955"]
    assert out[0]["seq"] == 1


def test_dedupe_queue_keeps_versions_and_kinds_apart():
    assert len(dedupe_queue([item(1, "A", "Follow It Up", "1"), item(2, "A", "Follow It Up (Dandana Mix)", "2")])) == 2
    assert len(dedupe_queue([item(1, "A", "T", "1"), item(2, "A", "T", "2", kind="release")])) == 2


# dedupe_matches

def entry(seq, title, rid, video_id, matched_title=None, source="am_search", artist="A"):
    e = item(seq, artist, title, rid)
    e.update(video_id=video_id, matched_artist=artist, matched_title=matched_title or title,
             source=source, artist_score=1.0, title_score=1.0, versions_match=True, mixed=False)
    return e


def test_dedupe_matches_same_result_id():
    acc, bor, rej = dedupe_matches([entry(1, "T", "26131865", "s1"), entry(2, "T", "26131634", "s1")], [], [])
    assert len(acc) == 1
    assert rids(acc[0]) == ["26131865", "26131634"]


def test_dedupe_matches_borderline_copy_of_accepted():
    acc, bor, rej = dedupe_matches([entry(1, "Midnight on the Shore", "A", "s1")],
                                   [entry(2, "Midnight on the Shore", "B", "s2")], [])
    assert bor == []
    assert rids(acc[0]) == ["A", "B"]


def test_dedupe_matches_keeps_versions_apart():
    acc, _, _ = dedupe_matches([entry(1, "Vocoder", "1", "s1"), entry(2, "Vocoder (Remix 1)", "2", "s2")], [], [])
    assert len(acc) == 2


def test_dedupe_matches_rejected_not_merged_into_accepted():
    acc, bor, rej = dedupe_matches([entry(1, "T", "1", "v1")], [], [entry(2, "T", "2", "v1")])
    assert len(acc) == 1 and len(rej) == 1


# unobtained_rows and review_rows

def test_unobtained_rows_per_source():
    e = entry(1, "T", "1", "v1")
    e["sources"] = [{"rid": "1", "release": "R1", "label": "L1", "merged_release_ids": ["2", "3"]},
                    {"rid": "4", "release": "R4", "label": "L4", "merged_release_ids": []}]
    rows = unobtained_rows(e, "rejected")
    assert len(rows) == 2
    assert list(rows[0]) == ["artist", "track", "release", "label", "reason", "discogs_url",
                             "merged_release_ids", "n_merged"]
    assert (rows[0]["merged_release_ids"], rows[0]["n_merged"]) == ("2;3", 2)
    assert rows[1]["discogs_url"] == "https://www.discogs.com/release/4"


def test_unobtained_rows_without_sources():
    (row,) = unobtained_rows({"artist": "A", "title": "T", "rid": "9", "release": "R", "label": "L"}, "no_result")
    assert (row["track"], row["release"], row["label"]) == ("T", "R", "L")
    assert row["discogs_url"] == "https://www.discogs.com/release/9"


def test_review_rows_include():
    e = entry(1, "Down Time", "1", "v1")
    (row,) = review_rows([e], include=True)
    assert list(row) == REVIEW_COLUMNS
    assert row["Discogs Track"] == "Down Time"
    assert row["Discogs Release"] == "R1"


def test_review_rows_merged_without_include():
    e = entry(1, "3m1", "1", "v1")
    e["discogs_tracks"] = ["3m1", "3m1 "]
    e["sources"].append({"rid": "2", "release": "R2", "label": "L", "merged_release_ids": []})
    (row,) = review_rows([e], include=False)
    assert "Include" not in row
    assert row["Discogs Track"] == "3m1 / 3m1 "
    assert row["Releases"] == 2
