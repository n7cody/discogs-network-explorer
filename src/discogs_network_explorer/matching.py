"""dnx matching: text cleaning, match scoring, and dedupe of search queries and results."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, NamedTuple

ARTIST_REJECT = 0.5
TITLE_REJECT = 0.4
ARTIST_ACCEPT = 0.8
TITLE_ACCEPT = 0.85
HEAD_MATCH_SCORE = 0.9

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
_TRANSLIT = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "ß": "ss", "ł": "l", "Ł": "L",
                           "đ": "d", "Đ": "D", "œ": "oe", "Œ": "OE", "þ": "th", "Þ": "TH", "ð": "d"})
_APOSTROPHES = re.compile(r"[’'`ʼ‘]")
_ARTIST_SEP = re.compile(
    r"\s*(?:,|&|\+|/|\||;)\s*|\s+(?:and|x|feat\.?|ft\.?|featuring|vs\.?|with|aka|presents|pres\.)\s+")
_DISC = re.compile(r"\s*[-–]\s*(?:disc|cd)\s*\d+\b|\s*[\(\[]\s*(?:disc|cd)\s*\d+\s*[\)\]]")
_GROUP = re.compile(r"[\(\[]([^\(\)\[\]]*)[\)\]]")
_LEAD_TAG = re.compile(r"^\s*[\[\(][^\]\)]*[\]\)]\s*(?=\S)")
_LEAD_PREMIERE = re.compile(r"^\s*(?:premiere|exclusive|free download)\s*[:|\-–—]\s*")
_SEP_SPLIT = re.compile(r"^(.+?)\s+[-–—|~]\s+(.+)$")
_QUOTED = re.compile(r"^(.+?)\s+[\"“‘'](.+)[\"”’']$")
_CREDIT_GROUP = re.compile(r"^(?:feat|ft|featuring|with|by)\b")
_TRAILING_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$")
_VERSION = re.compile(r"\b(?:remix|rmx|mix|edit|re ?edit|dub|version|rework|refix|vip|instrumental|live|"
                      r"extended|reprise|excerpt|flip|demo|acoustic|redux|bootleg)\b")
_MIXED = re.compile(r"^(?:mixed|mixed version|dj mix|continuous mix|mixed by .+)$")
_NOISE = re.compile(r"^(?:bonus|bonus track|original|original mix|album version|single version|explicit|clean|"
                    r"official|official video|official music video|official audio|audio|hd|hq|4k|"
                    r"visualizer|visualiser|lyric video|lyrics|premiere|full ep|full album|"
                    r"(?:\d{4} )?remaster(?:ed)?(?: \d{4})?(?: version)?)$")


def fold(s: str | None) -> str:
    """Unicode-fold text: NFKC, zero-width removal, Latin accents dropped, casefolded, spaces collapsed."""
    s = unicodedata.normalize("NFKC", s or "").translate(_ZERO_WIDTH).translate(_TRANSLIT)
    out = []
    for ch in unicodedata.normalize("NFD", s):
        if unicodedata.combining(ch) and out and unicodedata.name(out[-1], "").startswith("LATIN"):
            continue
        out.append(ch)
    s = unicodedata.normalize("NFC", "".join(out)).casefold()
    return " ".join(s.split())


def squash(s: str) -> str:
    """Delete apostrophes, turn other punctuation into spaces, collapse whitespace."""
    s = _APOSTROPHES.sub("", s)
    return " ".join(re.sub(r"[^\w\s]", " ", s).split())


def clean_artist(name: str | None) -> str:
    """Drop Discogs "(n)" suffixes, a trailing " - Topic" and a trailing "VEVO"; case is preserved."""
    s = re.sub(r"\s*\(\d+\)", "", name or "")
    s = re.sub(r"\s+-\s+topic\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*vevo\s*$", "", s, flags=re.IGNORECASE)
    return " ".join(s.split())


def artist_parts(name: str | None) -> list[str]:
    """Folded, squashed individual artist names from a credit string, in order, without duplicates."""
    parts = []
    for p in _ARTIST_SEP.split(fold(clean_artist(name))):
        p = squash(p)
        p = p[4:] if p.startswith("the ") else p
        if p and p not in parts:
            parts.append(p)
    return parts


def ratio(a: str, b: str) -> float:
    """SequenceMatcher ratio; 0.0 when both strings are empty."""
    return SequenceMatcher(None, a, b).ratio() if a or b else 0.0


def artist_score(discogs_artist: str, result_artist: str) -> float:
    """1.0 when any artist part is shared, else the best whole or pairwise ratio."""
    d, r = artist_parts(discogs_artist), artist_parts(result_artist)
    if not d or not r:
        return 0.0
    if set(d) & set(r):
        return 1.0
    return max([ratio(" ".join(d), " ".join(r))] + [ratio(x, y) for x in d for y in r])


class TitleParts(NamedTuple):
    base: str
    versions: frozenset
    mixed: bool
    head: str
    bracketed: bool


def _strip_artist_prefix(t: str, prefixes: set[str]) -> str:
    if not prefixes:
        return t
    for pattern in (_SEP_SPLIT, _QUOTED):
        m = pattern.match(t)
        if m and set(artist_parts(m.group(1))) & prefixes:
            return m.group(2).strip("\"“”‘’' ")
    m = re.match(r"^(.+?)\s+by\s+(.+)$", t)
    if m and set(artist_parts(m.group(2))) & prefixes:
        return m.group(1)
    return t


def split_title(title: str | None, strip_prefixes: Iterable[str] = ()) -> TitleParts:
    """Split a title into base text, version groups and the Mixed flag; digits are never stripped."""
    t = fold(title)
    t = _LEAD_TAG.sub("", t)
    t = _LEAD_PREMIERE.sub("", t)
    t = _strip_artist_prefix(t, set(strip_prefixes))
    t = _DISC.sub("", t)
    m = _SEP_SPLIT.match(t)
    if m and "(" not in m.group(2) and "[" not in m.group(2):
        tail = squash(m.group(2))
        if _NOISE.match(tail) or _MIXED.match(tail) or _VERSION.search(tail):
            t = f"{m.group(1)} ({m.group(2)})"
    head = squash(re.split(r"[\(\[]", t, maxsplit=1)[0])
    bracketed = bool(_GROUP.search(t))
    versions: set[str] = set()
    mixed = False

    def group(mt: re.Match) -> str:
        nonlocal mixed
        inner = squash(mt.group(1))
        if not inner or _CREDIT_GROUP.match(inner) or _NOISE.match(inner):
            return " "
        if _MIXED.match(inner):
            mixed = True
            return " "
        if _VERSION.search(inner):
            versions.add(inner)
            return " "
        return f" {inner} "

    t = _GROUP.sub(group, t)
    t = _TRAILING_FEAT.sub("", t)
    base = squash(t) or squash(fold(title))
    return TitleParts(base, frozenset(versions), mixed, head, bracketed)


class TitleMatch(NamedTuple):
    score: float
    versions_match: bool
    mixed: bool


def title_score(discogs_title: str, result_title: str, strip_prefixes: Iterable[str] = ()) -> TitleMatch:
    """Base-text ratio, lifted to HEAD_MATCH_SCORE when an unbracketed Discogs title equals the result's head."""
    d = split_title(discogs_title)
    r = split_title(result_title, strip_prefixes)
    score = ratio(d.base, r.base)
    if not d.bracketed and d.base and d.base == r.head:
        score = max(score, HEAD_MATCH_SCORE)
    return TitleMatch(score, d.versions == r.versions, r.mixed and not d.mixed)


def parse_youtube(title: str, channel: str) -> tuple[str, str]:
    """Guess (artist, title) from a YouTube video title, falling back to the channel name."""
    t = _LEAD_PREMIERE.sub("", _LEAD_TAG.sub("", title or ""))
    m = _SEP_SPLIT.match(t)
    return (m.group(1), m.group(2)) if m else (channel or "", t)


@dataclass(frozen=True)
class Verdict:
    verdict: str
    artist_score: float
    title_score: float
    versions_match: bool
    mixed: bool


def score_match(discogs_artist, discogs_title, result_artist, result_title, source="am_search",
                query_kind="track") -> Verdict:
    """Score a search result against a Discogs track: reject, borderline or accept.

    Release-level queries (query_kind "release") are never accepted automatically."""
    prefixes = set(artist_parts(discogs_artist))
    a = artist_score(discogs_artist, result_artist)
    if source == "yt_search":
        guess, _ = parse_youtube(result_title, result_artist)
        g = artist_score(discogs_artist, guess)
        a = max(a, g)
        if g >= ARTIST_ACCEPT:
            prefixes |= set(artist_parts(guess))
    tm = title_score(discogs_title, result_title, prefixes)
    if a < ARTIST_REJECT or tm.score < TITLE_REJECT:
        v = "reject"
    elif query_kind == "track" and a >= ARTIST_ACCEPT and tm.score >= TITLE_ACCEPT and tm.versions_match:
        v = "accept"
    else:
        v = "borderline"
    return Verdict(v, round(a, 2), round(tm.score, 2), tm.versions_match, tm.mixed)


ALBUM_DESCRIPTIONS = frozenset({"Album", "LP", "Mini-Album", "EP", "Compilation"})


def _entry(t: dict, artist: str) -> dict:
    return {"position": (t.get("position") or "").strip(), "title": (t.get("title") or "").strip(),
            "artist": artist, "duration": (t.get("duration") or "").strip()}


def playable_tracks(tracklist: list[dict]) -> list[dict]:
    """Searchable entries: sub-tracks replace index tracks; headings and empty titles are dropped."""
    positioned = any((t.get("position") or "").strip() for t in tracklist)
    out = []
    for t in tracklist:
        subs = t.get("sub_tracks") or []
        kind = t.get("type") or "track"
        if subs:
            out.extend(_entry(s, s.get("artist") or t.get("artist") or "") for s in subs)
            continue
        if kind in ("heading", "index"):
            continue
        e = _entry(t, t.get("artist") or "")
        if positioned and not e["position"] and not e["duration"]:
            continue
        out.append(e)
    return [e for e in out if e["title"]]


def release_level_listing(release: dict) -> bool:
    """True when the tracklist is empty or is one entry that names the album itself."""
    tracks = release.get("tracklist") or []
    if not tracks:
        return True
    if len(tracks) != 1:
        return False
    descs = set(release.get("format_descriptions") or [])
    return (bool(descs & ALBUM_DESCRIPTIONS)
            and split_title(tracks[0]["title"]).base == split_title(release.get("release_title")).base)


def build_query(artist: str, title: str) -> str:
    """Search query text: cleaned artist, a dash, then the title."""
    return f"{clean_artist(artist)} - {(title or '').strip()}"


def query_key(item: dict) -> tuple:
    """Key that identifies identical searches (query kind, artist parts, folded title)."""
    return (item.get("query_kind", "track"), " ".join(sorted(artist_parts(item["artist"]))),
            squash(fold(item["title"])))


def _merge_sources(kept: dict, other: dict) -> None:
    have = {s["rid"] for s in kept["sources"]}
    kept["sources"].extend(s for s in other["sources"] if s["rid"] not in have)
    tracks = kept.setdefault("discogs_tracks", [kept["discogs_track"]])
    if other["discogs_track"] not in tracks:
        tracks.append(other["discogs_track"])


def dedupe_queue(items: list[dict]) -> list[dict]:
    """One search per query key; later duplicates merge their sources into the first."""
    kept: dict[tuple, dict] = {}
    out = []
    for item in sorted(items, key=lambda i: i["seq"]):
        k = query_key(item)
        if k in kept:
            _merge_sources(kept[k], item)
            continue
        item = {**item, "sources": list(item["sources"])}
        kept[k] = item
        out.append(item)
    return out


def match_key(entry: dict) -> tuple:
    """Key that identifies the same matched recording for one Discogs artist."""
    prefixes = set(artist_parts(entry["artist"])) | set(artist_parts(entry.get("matched_artist")))
    tp = split_title(entry["matched_title"], prefixes)
    return (" ".join(sorted(artist_parts(entry["artist"]))), tp.base, tp.versions, tp.mixed)


def dedupe_matches(accepted, borderline, rejected):
    """Merge duplicate matches by result ID or match key.

    Accepted and borderline share one key map, so a borderline copy of an accepted match is
    dropped. Rejected entries are deduped only among themselves."""
    seen: dict[tuple, dict] = {}
    out = ([], [], [])

    def run(entries, bucket, keys_seen):
        for e in sorted(entries, key=lambda x: x["seq"]):
            keys = [("id", e["source"], e["video_id"]), ("text",) + match_key(e)]
            hit = next((keys_seen[k] for k in keys if k in keys_seen), None)
            if hit is not None:
                _merge_sources(hit, e)
                continue
            e = {**e, "sources": list(e["sources"])}
            for k in keys:
                keys_seen[k] = e
            bucket.append(e)

    run(accepted, out[0], seen)
    run(borderline, out[1], seen)
    run(rejected, out[2], {})
    return out


_VERDICT_RANK = {"reject": 0, "borderline": 1, "accept": 2}


def pick_best(item: dict, candidates: list[dict], source: str) -> tuple[dict | None, Verdict | None]:
    """Best candidate: highest verdict, then not "(Mixed)", then artist + title score; ties keep search order."""
    best, best_v, best_key = None, None, None
    for c in candidates:
        v = score_match(item["artist"], item["title"], c.get("artist", ""), c.get("title", ""),
                        source, item.get("query_kind", "track"))
        key = (_VERDICT_RANK[v.verdict], not v.mixed, v.artist_score + v.title_score)
        if best_key is None or key > best_key:
            best, best_v, best_key = c, v, key
    return best, best_v


def release_source(rel: dict) -> dict:
    """The source record (rid, release, label, merged_release_ids) for a catalog release."""
    return {"rid": str(rel["release_id"]), "release": rel.get("release_title") or "",
            "label": rel.get("label_name") or "",
            "merged_release_ids": list(rel.get("merged_release_ids") or [])}


def search_items(rel: dict, release_fallback: bool) -> list[dict]:
    """Search items for a catalog release (without seq); release-level items search the album title."""
    src = release_source(rel)
    base = {"release": src["release"], "label": src["label"], "rid": src["rid"], "sources": [src]}
    tracks = [t for t in rel.get("tracklist") or [] if t.get("artist") and t.get("title")]
    if tracks and not release_level_listing(rel):
        return [{**base, "artist": t["artist"], "title": t["title"], "discogs_track": t["title"],
                 "query_kind": "track", "sources": [src]} for t in tracks]
    if tracks:
        t = tracks[0]
        return [{**base, "artist": t["artist"], "title": t["title"], "discogs_track": t["title"],
                 "query_kind": "release"}]
    if not rel.get("tracklist") and release_fallback and rel.get("artist_name") and src["release"]:
        return [{**base, "artist": rel["artist_name"], "title": src["release"],
                 "discogs_track": src["release"], "query_kind": "release"}]
    return []


def make_entry(item: dict, result: dict, verdict: Verdict, source: str) -> dict:
    """A search entry: the item's Discogs fields plus the chosen result and its scores."""
    return {
        "seq": item["seq"],
        "video_id": result["song_id"] if source == "am_search" else result["video_id"],
        "title": result["title"],
        "artist": item["artist"],
        "matched_artist": result.get("artist", ""),
        "matched_title": result["title"],
        "release": item["release"],
        "label": item["label"],
        "rid": item["rid"],
        "discogs_track": item["discogs_track"],
        "query_kind": item.get("query_kind", "track"),
        "sources": list(item["sources"]),
        "source": source,
        "artist_score": verdict.artist_score,
        "title_score": verdict.title_score,
        "versions_match": verdict.versions_match,
        "mixed": verdict.mixed,
    }


def _sources(entry: dict) -> list[dict]:
    return entry.get("sources") or [{"rid": str(entry.get("rid") or ""), "release": entry.get("release", ""),
                                      "label": entry.get("label", ""), "merged_release_ids": []}]


def unobtained_rows(entry: dict, reason: str) -> list[dict]:
    """One dnx_unobtained.csv row per source release of an item or entry."""
    track = entry.get("discogs_track") or entry.get("title", "")
    return [{
        "artist": entry.get("artist", ""),
        "track": track,
        "release": s["release"],
        "label": s["label"],
        "reason": reason,
        "discogs_url": f"https://www.discogs.com/release/{s['rid']}" if s["rid"] else "",
        "merged_release_ids": ";".join(s["merged_release_ids"]),
        "n_merged": len(s["merged_release_ids"]),
    } for s in _sources(entry)]


REVIEW_COLUMNS = ["Include", "Discogs Artist", "Discogs Track", "Discogs Release", "Searched As",
                  "Match Artist", "Match Track", "Artist Score", "Track Score", "Version Match",
                  "Releases", "Release"]


def review_rows(entries: list[dict], include: bool) -> list[dict]:
    """Rows for the review or rejected tables, in REVIEW_COLUMNS order (Include only when include)."""
    rows = []
    for e in entries:
        srcs = _sources(e)
        row = {
            "Include": False,
            "Discogs Artist": e.get("artist", ""),
            "Discogs Track": " / ".join(e.get("discogs_tracks") or [e.get("discogs_track", "")]),
            "Discogs Release": e.get("release", ""),
            "Searched As": e.get("query_kind", "track"),
            "Match Artist": e.get("matched_artist", ""),
            "Match Track": e.get("matched_title", ""),
            "Artist Score": e.get("artist_score"),
            "Track Score": e.get("title_score"),
            "Version Match": e.get("versions_match", True),
            "Releases": len(srcs),
            "Release": f"https://www.discogs.com/release/{srcs[0]['rid']}" if srcs[0]["rid"] else "",
        }
        if not include:
            del row["Include"]
        rows.append(row)
    return rows
