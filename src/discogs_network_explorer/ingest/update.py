"""Download a Dump, build a database next to the live one, and swap it in."""

from __future__ import annotations

import gzip
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import BinaryIO

from .build import build_snapshot, read_meta, shrunken_tables
from .download import KINDS, HttpSource, dump_filename, sha256_file
from .parse import parse_dump

log = logging.getLogger(__name__)

LOG_EVERY_S = 60


class UpdateError(RuntimeError):
    pass


def default_db_dir() -> Path:
    env = os.environ.get("DNE_DB_PATH")
    return Path(env).expanduser() if env else Path.home() / ".dne" / "discogs_db"


def live_snapshot(db_dir: Path) -> Path | None:
    """The newest `discogs_YYYYMMDD.duckdb` in `db_dir`, if any."""
    found = sorted(db_dir.glob("discogs_[0-9]*.duckdb"))
    return found[-1] if found else None


class _CountingReader:
    def __init__(self, raw: BinaryIO):
        self.raw = raw
        self.count = 0

    def read(self, n: int = -1) -> bytes:
        data = self.raw.read(n)
        self.count += len(data)
        return data


def _progress_logger(kind: str, counter: _CountingReader, total_bytes: int | None, limit: int | None):
    t0 = last = time.perf_counter()

    def progress(n: int) -> None:
        nonlocal last
        now = time.perf_counter()
        if now - last < LOG_EVERY_S:
            return
        last = now
        elapsed = now - t0
        if limit:
            frac = n / limit
        elif total_bytes:
            frac = counter.count / total_bytes
        else:
            frac = 0
        eta = elapsed * (1 / frac - 1) / 60 if frac else float("nan")
        log.info("%s: %d parsed, %.0f/s, ETA %.0f min", kind, n, n / elapsed, eta)

    return progress


def _parse_file(kind: str, raw: BinaryIO, staging: Path, total_bytes: int | None,
                limit: int | None) -> int:
    counter = _CountingReader(raw)
    with gzip.GzipFile(fileobj=counter) as xml:
        return parse_dump(kind, xml, staging, limit=limit,
                          progress=_progress_logger(kind, counter, total_bytes, limit))


def run_update(db_dir: Path, source: HttpSource, dump: str | None = None, force: bool = False,
               keep_downloads: bool = False, limit: int | None = None,
               memory_limit: str | None = None) -> Path:
    """Build the database for `dump` (default: latest) and make it the live one.

    Returns the live database path. With `limit`, releases are streamed and only
    the first `limit` are parsed (dev runs); such a build will not replace a full
    database unless `force` is set.
    """
    db_dir.mkdir(parents=True, exist_ok=True)
    dump_date = dump or source.latest_date()
    if not re.fullmatch(r"\d{8}", dump_date):
        raise UpdateError(f"dump date must be YYYYMMDD, got {dump_date[:30]!r}")
    live = live_snapshot(db_dir)
    live_meta = read_meta(live) if live else None

    if live_meta and not force:
        live_full = live_meta["release_limit"] is None
        if limit is not None and live_full:
            raise UpdateError(f"{live.name} is a full database; a --limit build would replace it "
                              "(use --force, or --db-path for a separate directory)")
        if limit is None and live_full and live_meta["dump_date"] == dump_date:
            log.info("database already at dump %s: %s", dump_date, live)
            return live

    log.info("building database for dump %s in %s", dump_date, db_dir)
    timings: dict[str, float] = {}
    checksums = {k: v for k, v in source.checksums(dump_date).items()
                 if k in {dump_filename(dump_date, kind) for kind in KINDS}}
    downloads = db_dir / "downloads"
    staging = db_dir / "staging"
    downloads.mkdir(exist_ok=True)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()

    t0 = time.perf_counter()
    stream_releases = limit is not None
    for kind in KINDS:
        if kind == "releases" and stream_releases:
            continue
        name = dump_filename(dump_date, kind)
        dest = downloads / name
        if dest.exists() and sha256_file(dest) == checksums[name]:
            log.info("using existing download %s", dest)
            continue
        source.download(name, dest, checksums[name])
    timings["download"] = round(time.perf_counter() - t0, 1)

    for kind in KINDS:
        t0 = time.perf_counter()
        name = dump_filename(dump_date, kind)
        if kind == "releases" and stream_releases:
            log.info("streaming %s (first %d releases)", name, limit)
            with source.open(name) as raw:
                _parse_file(kind, raw, staging, None, limit)
        else:
            path = downloads / name
            with open(path, "rb") as raw:
                _parse_file(kind, raw, staging, path.stat().st_size, None)
        timings[f"parse_{kind}"] = round(time.perf_counter() - t0, 1)

    final = db_dir / f"discogs_{dump_date}.duckdb"
    tmp = final.with_name(final.name + ".tmp")
    counts = build_snapshot(staging, tmp, dump_date, checksums, timings, release_limit=limit,
                            memory_limit=memory_limit)

    if live_meta and live_meta["release_limit"] is None and limit is None:
        shrunk = shrunken_tables(counts, live_meta["row_counts"])
        if shrunk:
            tmp.unlink(missing_ok=True)
            raise UpdateError(f"row counts fell below 95% of {live.name} for: {', '.join(shrunk)}; "
                              f"kept the old database (staging left in {staging})")

    tmp.replace(final)
    for old in db_dir.glob("discogs_[0-9]*.duckdb"):
        if old != final:
            old.unlink()
            Path(str(old) + ".wal").unlink(missing_ok=True)
    shutil.rmtree(staging, ignore_errors=True)
    if not keep_downloads:
        shutil.rmtree(downloads, ignore_errors=True)
    log.info("live database: %s (%d releases)", final, counts["releases"])
    return final
