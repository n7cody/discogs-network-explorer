"""Stream Discogs XML into Parquet files, one per table."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree

log = logging.getLogger(__name__)

BATCH = 50_000
CHUNK = 1 << 20

_S = pa.string()
_I32, _I64 = pa.int32(), pa.int64()

SCHEMAS: dict[str, pa.Schema] = {k: pa.schema(v) for k, v in {
    "releases": [("release_id", _I64), ("title", _S), ("country", _S), ("released", _S),
                 ("year", _I32), ("master_id", _I64), ("is_main_release", pa.bool_()),
                 ("data_quality", _S), ("genres", pa.list_(_S)), ("styles", pa.list_(_S)),
                 ("is_unofficial", pa.bool_())],
    "release_formats": [("release_id", _I64), ("seq", _I32), ("name", _S), ("qty", _I32),
                        ("text", _S), ("descriptions", pa.list_(_S))],
    "release_labels": [("release_id", _I64), ("label_id", _I64), ("label_name", _S), ("catno", _S)],
    "release_artists": [("release_id", _I64), ("seq", _I32), ("artist_id", _I64),
                        ("artist_name", _S), ("anv", _S)],
    "tracks": [("release_id", _I64), ("seq", _I32), ("position", _S), ("title", _S),
               ("duration", _S)],
    "track_artists": [("release_id", _I64), ("track_seq", _I32), ("artist_id", _I64),
                      ("artist_name", _S)],
    "credits": [("release_id", _I64), ("track_seq", _I32), ("artist_id", _I64),
                ("artist_name", _S), ("role", _S)],
    "release_videos": [("release_id", _I64), ("seq", _I32), ("url", _S), ("title", _S),
                       ("duration", _I32)],
    "artists": [("artist_id", _I64), ("name", _S)],
    "labels": [("label_id", _I64), ("name", _S), ("parent_label_id", _I64)],
    "masters": [("master_id", _I64), ("main_release_id", _I64), ("year", _I32), ("title", _S)],
}.items()}

TABLES_BY_KIND: dict[str, tuple[str, ...]] = {
    "releases": ("releases", "release_formats", "release_labels", "release_artists", "tracks",
                 "track_artists", "credits", "release_videos"),
    "artists": ("artists",),
    "labels": ("labels",),
    "masters": ("masters",),
}


def _int(s: str | None, bits: int = 64) -> int | None:
    """Parse an integer; None if missing, malformed, or outside the column's signed range."""
    try:
        n = int(s)
    except (TypeError, ValueError):
        return None
    return n if -(1 << (bits - 1)) <= n < (1 << (bits - 1)) else None


def _positive(n: int | None) -> int | None:
    return n if n and n > 0 else None


def _people(parent) -> list[dict[str, str | None]]:
    if parent is None:
        return []
    return [{c.tag: c.text for c in a} for a in parent]


def _release(el, out: dict[str, list]) -> None:
    rid = int(el.get("id"))
    k = {c.tag: c for c in el}
    t = lambda tag: k[tag].text if tag in k else None
    released = t("released")
    m = k.get("master_id")
    master_id = _positive(_int(m.text)) if m is not None else None

    unofficial = False
    for i, f in enumerate(el.iterfind("formats/format")):
        descs = [d.text for d in f.iterfind("descriptions/description")]
        unofficial |= "Unofficial Release" in descs
        out["release_formats"].append({"release_id": rid, "seq": i, "name": f.get("name"),
                                       "qty": _int(f.get("qty"), 32), "text": f.get("text"),
                                       "descriptions": descs})
    out["releases"].append({
        "release_id": rid, "title": t("title"), "country": t("country"), "released": released,
        "year": _positive(_int((released or "")[:4], 32)), "master_id": master_id,
        "is_main_release": (m.get("is_main_release") == "true") if master_id else None,
        "data_quality": t("data_quality"),
        "genres": [g.text for g in el.iterfind("genres/genre")],
        "styles": [s.text for s in el.iterfind("styles/style")],
        "is_unofficial": unofficial,
    })
    for lab in el.iterfind("labels/label"):
        out["release_labels"].append({"release_id": rid, "label_id": _int(lab.get("id")),
                                      "label_name": lab.get("name"), "catno": lab.get("catno")})
    for i, a in enumerate(_people(k.get("artists"))):
        out["release_artists"].append({"release_id": rid, "seq": i, "artist_id": _int(a.get("id")),
                                       "artist_name": a.get("name"), "anv": a.get("anv")})
    for a in _people(k.get("extraartists")):
        out["credits"].append({"release_id": rid, "track_seq": None, "artist_id": _int(a.get("id")),
                               "artist_name": a.get("name"), "role": a.get("role")})
    for i, tr in enumerate(el.iterfind("tracklist/track")):
        tk = {c.tag: c for c in tr}
        tt = lambda tag: tk[tag].text if tag in tk else None
        out["tracks"].append({"release_id": rid, "seq": i, "position": tt("position"),
                              "title": tt("title"), "duration": tt("duration")})
        for a in _people(tk.get("artists")):
            out["track_artists"].append({"release_id": rid, "track_seq": i,
                                         "artist_id": _int(a.get("id")), "artist_name": a.get("name")})
        for a in _people(tk.get("extraartists")):
            out["credits"].append({"release_id": rid, "track_seq": i, "artist_id": _int(a.get("id")),
                                   "artist_name": a.get("name"), "role": a.get("role")})
    for i, v in enumerate(el.iterfind("videos/video")):
        src = v.get("src") or ""
        if "youtube.com" in src or "youtu.be" in src:
            out["release_videos"].append({"release_id": rid, "seq": i, "url": src,
                                          "title": v.findtext("title"),
                                          "duration": _int(v.get("duration"), 32)})


def _artist(el, out: dict[str, list]) -> None:
    out["artists"].append({"artist_id": _int(el.findtext("id")), "name": el.findtext("name")})


def _label(el, out: dict[str, list]) -> None:
    p = el.find("parentLabel")
    out["labels"].append({"label_id": _int(el.findtext("id")), "name": el.findtext("name"),
                          "parent_label_id": _int(p.get("id")) if p is not None else None})


def _master(el, out: dict[str, list]) -> None:
    out["masters"].append({"master_id": int(el.get("id")),
                           "main_release_id": _int(el.findtext("main_release")),
                           "year": _positive(_int(el.findtext("year"), 32)),
                           "title": el.findtext("title")})


HANDLERS: dict[str, Callable] = {"releases": _release, "artists": _artist,
                                 "labels": _label, "masters": _master}


def iter_records(stream: BinaryIO, tag: str) -> Iterator:
    """Yield each direct child of the root named `tag`, freeing memory as it goes.

    Nested elements with the same tag (sublabels inside a label) are skipped.
    """
    parser = etree.XMLPullParser(events=("end",), tag=tag, huge_tree=True,
                                resolve_entities=False, no_network=True)
    while chunk := stream.read(CHUNK):
        parser.feed(chunk)
        for _, el in parser.read_events():
            parent = el.getparent()
            if parent is None or parent.getparent() is not None:
                continue
            yield el
            el.clear()
            while el.getprevious() is not None:
                del parent[0]
    parser.close()


def parse_xml(kind: str, stream: BinaryIO, out_dir: Path, limit: int | None = None,
               progress: Callable[[int], None] | None = None) -> int:
    """Parse one Discogs XML file (decompressed XML stream) into `<out_dir>/<table>.parquet`.

    Returns the number of records parsed. `progress` is called with the running
    count after every batch.
    """
    if kind not in HANDLERS:
        raise ValueError(f"unknown file kind: {kind}")
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = TABLES_BY_KIND[kind]
    handle = HANDLERS[kind]
    buf: dict[str, list] = {t: [] for t in tables}
    writers = {t: pq.ParquetWriter(out_dir / f"{t}.parquet", SCHEMAS[t], compression="zstd")
               for t in tables}

    def flush() -> None:
        for t, rows in buf.items():
            if rows:
                writers[t].write_table(pa.Table.from_pylist(rows, schema=SCHEMAS[t]))
                rows.clear()

    n = 0
    t0 = time.perf_counter()
    try:
        for el in iter_records(stream, kind[:-1]):
            handle(el, buf)
            n += 1
            if n % BATCH == 0:
                flush()
                if progress:
                    progress(n)
            if limit is not None and n >= limit:
                break
        flush()
    finally:
        for w in writers.values():
            w.close()
    log.info("parsed %s: %d records in %.0f s", kind, n, time.perf_counter() - t0)
    return n
