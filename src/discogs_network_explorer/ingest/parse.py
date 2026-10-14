"""Stream Discogs XML into Parquet files, one per table."""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import queue
import re
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree

log = logging.getLogger(__name__)

BATCH = 50_000
CHUNK = 1 << 20
TASK_RECORDS = 1_000
WORKER_FLUSH = 20_000
VARIANTS = ("trimmed", "full")

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
    "sub_tracks": [("release_id", _I64), ("track_seq", _I32), ("seq", _I32), ("position", _S),
                   ("title", _S), ("duration", _S)],
    "track_artists": [("release_id", _I64), ("track_seq", _I32), ("artist_id", _I64),
                      ("artist_name", _S)],
    "credits": [("release_id", _I64), ("track_seq", _I32), ("artist_id", _I64),
                ("artist_name", _S), ("role", _S)],
    "role_credits": [("release_id", _I64), ("artist_id", _I64), ("role", _S)],
    "release_videos": [("release_id", _I64), ("seq", _I32), ("url", _S), ("title", _S),
                       ("duration", _I32)],
    "artists": [("artist_id", _I64), ("name", _S)],
    "labels": [("label_id", _I64), ("name", _S), ("parent_label_id", _I64)],
    "masters": [("master_id", _I64), ("main_release_id", _I64), ("year", _I32), ("title", _S)],
}.items()}

TABLES_BY_KIND: dict[str, tuple[str, ...]] = {
    "releases": ("releases", "release_formats", "release_labels", "release_artists", "tracks",
                 "sub_tracks", "track_artists", "credits", "release_videos"),
    "artists": ("artists",),
    "labels": ("labels",),
    "masters": ("masters",),
}

# Columns the app never reads; the trimmed variant leaves them out
DROPPED_COLUMNS = {
    "releases": ("released", "data_quality"),
    "release_formats": ("qty", "text"),
    "release_labels": ("catno",),
    "release_artists": ("anv",),
    "release_videos": ("duration",),
}


@dataclass(frozen=True)
class IngestOptions:
    """What a build keeps. `trimmed` drops what the app never reads; `full` keeps everything."""

    variant: str = "trimmed"
    cores: int = 1

    def __post_init__(self):
        if self.variant not in VARIANTS:
            raise ValueError(f"unknown variant: {self.variant!r} (expected one of {', '.join(VARIANTS)})")
        if self.cores < 1:
            raise ValueError(f"cores must be at least 1, got {self.cores}")

    @property
    def trimmed(self) -> bool:
        return self.variant == "trimmed"

    @property
    def kinds(self) -> tuple[str, ...]:
        """Database files to download and parse, releases last."""
        kinds = ("labels", "artists", "masters", "releases")
        return tuple(k for k in kinds if k != "masters") if self.trimmed else kinds

    def as_json(self) -> dict:
        t = self.trimmed
        return {**asdict(self), "master_rule": t, "role_credits_only": t, "drop_columns": t,
                "skip_masters": t, "prune_unreferenced": t}


def default_cores() -> int:
    return max(1, min(os.cpu_count() or 1, 4))


def tables_for(kind: str, opts: IngestOptions) -> tuple[str, ...]:
    tables = TABLES_BY_KIND[kind]
    if opts.trimmed:
        tables = tuple("role_credits" if t == "credits" else t for t in tables)
    return tables


def schemas(opts: IngestOptions) -> dict[str, pa.Schema]:
    """Parsed tables of a build and their schemas, release tables first."""
    out = {}
    for kind in ("releases", "artists", "labels", "masters"):
        if kind not in opts.kinds:
            continue
        for t in tables_for(kind, opts):
            schema = SCHEMAS[t]
            if opts.trimmed:
                for col in DROPPED_COLUMNS.get(t, ()):
                    schema = schema.remove(schema.get_field_index(col))
            out[t] = schema
    return out


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


_BRACKETED = re.compile(r"[\t\n\f\r ]*\[[^\]]*\]")
_ROLE_NAMES = {"producer": "Producer", "co-producer": "Co-producer", "remix": "Remix"}


def base_roles(role: str | None) -> list[str]:
    """Producer / Co-producer / Remix named in a credit role; same rule as the SQL in build.py."""
    if not role:
        return []
    parts = (p.strip(" ").lower() for p in _BRACKETED.sub("", role).split(","))
    return [_ROLE_NAMES[p] for p in parts if p in _ROLE_NAMES]


def _credit(out: dict[str, list], trimmed: bool, seen: set, rid: int, track_seq: int | None,
            a: dict) -> None:
    artist_id = _int(a.get("id"))
    if not trimmed:
        out["credits"].append({"release_id": rid, "track_seq": track_seq, "artist_id": artist_id,
                               "artist_name": a.get("name"), "role": a.get("role")})
        return
    if artist_id is None:
        return
    for role in base_roles(a.get("role")):
        if (artist_id, role) not in seen:
            seen.add((artist_id, role))
            out["role_credits"].append({"release_id": rid, "artist_id": artist_id, "role": role})


def _release(el, out: dict[str, list], trimmed: bool = False) -> None:
    rid = int(el.get("id"))
    k = {c.tag: c for c in el}
    t = lambda tag: k[tag].text if tag in k else None
    m = k.get("master_id")
    master_id = _positive(_int(m.text)) if m is not None else None
    is_main = (m.get("is_main_release") == "true") if master_id else None
    if trimmed and is_main is False:
        return
    released = t("released")
    seen: set = set()

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
        "is_main_release": is_main,
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
        _credit(out, trimmed, seen, rid, None, a)
    for i, tr in enumerate(el.iterfind("tracklist/track")):
        tk = {c.tag: c for c in tr}
        tt = lambda tag: tk[tag].text if tag in tk else None
        out["tracks"].append({"release_id": rid, "seq": i, "position": tt("position"),
                              "title": tt("title"), "duration": tt("duration")})
        if "sub_tracks" in tk:
            for j, st in enumerate(tk["sub_tracks"].iterfind("track")):
                sk = {c.tag: c for c in st}
                out["sub_tracks"].append({"release_id": rid, "track_seq": i, "seq": j,
                                          "position": sk["position"].text if "position" in sk else None,
                                          "title": sk["title"].text if "title" in sk else None,
                                          "duration": sk["duration"].text if "duration" in sk else None})
        for a in _people(tk.get("artists")):
            out["track_artists"].append({"release_id": rid, "track_seq": i,
                                         "artist_id": _int(a.get("id")), "artist_name": a.get("name")})
        for a in _people(tk.get("extraartists")):
            _credit(out, trimmed, seen, rid, i, a)
    for i, v in enumerate(el.iterfind("videos/video")):
        src = v.get("src") or ""
        if "youtube.com" in src or "youtu.be" in src:
            out["release_videos"].append({"release_id": rid, "seq": i, "url": src,
                                          "title": v.findtext("title"),
                                          "duration": _int(v.get("duration"), 32)})


def _artist(el, out: dict[str, list], trimmed: bool = False) -> None:
    out["artists"].append({"artist_id": _int(el.findtext("id")), "name": el.findtext("name")})


def _label(el, out: dict[str, list], trimmed: bool = False) -> None:
    p = el.find("parentLabel")
    out["labels"].append({"label_id": _int(el.findtext("id")), "name": el.findtext("name"),
                          "parent_label_id": _int(p.get("id")) if p is not None else None})


def _master(el, out: dict[str, list], trimmed: bool = False) -> None:
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


_RECORD_END = b"</release>"
_RECORD_START = re.compile(rb"<release[\s>]")
_NON_MAIN = b'<master_id is_main_release="false">'


def is_non_main_record(rec: bytes) -> bool:
    """A raw release with a master that is not the master's main release.

    Releases without a master are written as `<master_id is_main_release="false">0</master_id>`.
    """
    i = rec.find(_NON_MAIN)
    if i == -1:
        return False
    i += len(_NON_MAIN)
    return _positive(_int(rec[i:rec.find(b"<", i)])) is not None


def iter_release_bytes(stream: BinaryIO) -> Iterator[bytes]:
    """Yield each `<release>...</release>` record of a releases file as raw bytes.

    `</release>` cannot occur inside text (a literal `<` is always escaped), so
    records are cut there without parsing.
    """
    buf = b""
    while chunk := stream.read(CHUNK):
        buf += chunk
        start = 0
        while (end := buf.find(_RECORD_END, start)) != -1:
            end += len(_RECORD_END)
            m = _RECORD_START.search(buf, start, end)
            if m:
                yield buf[m.start():end]
            start = end
        buf = buf[start:]


def _writers(out_dir: Path, tables: dict[str, pa.Schema], name: str) -> dict:
    return {t: pq.ParquetWriter(out_dir / name.format(table=t), schema, compression="zstd")
            for t, schema in tables.items()}


def _flush(buf: dict[str, list], writers: dict, tables: dict[str, pa.Schema]) -> None:
    for t, rows in buf.items():
        if rows:
            writers[t].write_table(pa.Table.from_pylist(rows, schema=tables[t]))
            rows.clear()


def _release_worker(idx: int, tasks, results, out_dir: str, variant: str) -> None:
    """Parse batches of raw release records into `<out_dir>/<table>/part-<idx>.parquet`."""
    try:
        opts = IngestOptions(variant)
        all_schemas = schemas(opts)
        tables = {t: all_schemas[t] for t in tables_for("releases", opts)}
        writers = _writers(Path(out_dir), tables, f"{{table}}/part-{idx:03d}.parquet")
        buf: dict[str, list] = {t: [] for t in tables}
        parser = etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True)
        n = pending = 0
        try:
            while (batch := tasks.get()) is not None:
                root = etree.fromstring(b"<releases>" + batch + b"</releases>", parser)
                for el in root:
                    _release(el, buf, opts.trimmed)
                n += len(root)
                pending += len(root)
                if pending >= WORKER_FLUSH:
                    _flush(buf, writers, tables)
                    pending = 0
            _flush(buf, writers, tables)
        finally:
            for w in writers.values():
                w.close()
        results.put((idx, n, None))
    except BaseException:
        results.put((idx, 0, traceback.format_exc()))


def _put(tasks, item, procs) -> None:
    while True:
        try:
            tasks.put(item, timeout=1)
            return
        except queue.Full:
            if any(not p.is_alive() for p in procs):
                raise RuntimeError("a release parse worker stopped early") from None


def _collect(results, procs) -> list[tuple]:
    out = []
    while len(out) < len(procs):
        try:
            out.append(results.get(timeout=1))
        except queue.Empty:
            if all(not p.is_alive() for p in procs):
                try:
                    out.append(results.get(timeout=1))
                except queue.Empty:
                    raise RuntimeError("a release parse worker exited without a result") from None
    return out


def _parse_releases_parallel(stream: BinaryIO, out_dir: Path, opts: IngestOptions,
                             limit: int | None, progress: Callable[[int], None] | None) -> int:
    """Split records in this process; `opts.cores` worker processes parse them."""
    for t in tables_for("releases", opts):
        (out_dir / t).mkdir(parents=True, exist_ok=True)
    ctx = mp.get_context("spawn")
    tasks = ctx.Queue(maxsize=opts.cores * 4)
    results = ctx.Queue()
    procs = [ctx.Process(target=_release_worker, args=(i, tasks, results, str(out_dir), opts.variant),
                         daemon=True) for i in range(opts.cores)]
    for p in procs:
        p.start()
    n = 0
    batch: list[bytes] = []
    try:
        for rec in iter_release_bytes(stream):
            n += 1
            if not (opts.trimmed and is_non_main_record(rec)):
                batch.append(rec)
                if len(batch) >= TASK_RECORDS:
                    _put(tasks, b"".join(batch), procs)
                    batch = []
            if progress and n % BATCH == 0:
                progress(n)
            if limit is not None and n >= limit:
                break
        if batch:
            _put(tasks, b"".join(batch), procs)
        for _ in procs:
            _put(tasks, None, procs)
        errors = [err for _, _, err in _collect(results, procs) if err]
        if errors:
            raise RuntimeError(f"release parse worker failed:\n{errors[0]}")
        for p in procs:
            p.join()
    finally:
        for p in procs:
            if p.is_alive():
                p.terminate()
    return n


def parse_xml(kind: str, stream: BinaryIO, out_dir: Path, limit: int | None = None,
              progress: Callable[[int], None] | None = None,
              opts: IngestOptions | None = None) -> int:
    """Parse one Discogs XML file (decompressed XML stream) into Parquet under `out_dir`.

    Writes `<out_dir>/<table>.parquet`, or `<out_dir>/<table>/part-NNN.parquet` when
    releases are parsed with more than one core. Returns the number of records read
    (non-main releases skipped by the trimmed variant included). `progress` is called
    with the running count after every batch.
    """
    if kind not in HANDLERS:
        raise ValueError(f"unknown file kind: {kind}")
    opts = opts or IngestOptions()
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    if kind == "releases" and opts.cores > 1:
        n = _parse_releases_parallel(stream, out_dir, opts, limit, progress)
        log.info("parsed %s: %d records in %.0f s on %d cores", kind, n, time.perf_counter() - t0,
                 opts.cores)
        return n

    all_schemas = schemas(opts) if kind in opts.kinds else SCHEMAS
    tables = {t: all_schemas[t] for t in tables_for(kind, opts)}
    handle = HANDLERS[kind]
    buf: dict[str, list] = {t: [] for t in tables}
    writers = _writers(out_dir, tables, "{table}.parquet")
    n = 0
    try:
        for el in iter_records(stream, kind[:-1]):
            handle(el, buf, opts.trimmed)
            n += 1
            if n % BATCH == 0:
                _flush(buf, writers, tables)
                if progress:
                    progress(n)
            if limit is not None and n >= limit:
                break
        _flush(buf, writers, tables)
    finally:
        for w in writers.values():
            w.close()
    log.info("parsed %s: %d records in %.0f s", kind, n, time.perf_counter() - t0)
    return n
