"""Download a database version, build a database next to the live one, and swap it in."""

from __future__ import annotations

import gzip
import logging
import multiprocessing as mp
import os
import re
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import BinaryIO

from .build import SCHEMA_VERSION, build_snapshot, read_meta, shrunken_tables
from .download import HttpSource, dated_filename, sha256_file
from .parse import IngestOptions, parse_xml

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
                limit: int | None, opts: IngestOptions) -> int:
    counter = _CountingReader(raw)
    with gzip.GzipFile(fileobj=counter) as xml:
        return parse_xml(kind, xml, staging, limit=limit,
                         progress=_progress_logger(kind, counter, total_bytes, limit), opts=opts)


def _parse_downloaded(kinds: tuple[str, ...], downloads: Path, db_date: str, staging: Path,
                      opts: IngestOptions) -> dict[str, float]:
    """Parse downloaded files one after another; return a timing per kind."""
    timings = {}
    for kind in kinds:
        t0 = time.perf_counter()
        path = downloads / dated_filename(db_date, kind)
        with open(path, "rb") as raw:
            _parse_file(kind, raw, staging, path.stat().st_size, None, opts)
        timings[f"parse_{kind}"] = round(time.perf_counter() - t0, 1)
    return timings


def _parse_downloaded_in_worker(*args) -> dict[str, float]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    return _parse_downloaded(*args)


def run_update(db_dir: Path, source: HttpSource, date: str | None = None, force: bool = False,
               keep_downloads: bool = False, limit: int | None = None,
               memory_limit: str | None = None, opts: IngestOptions | None = None) -> Path:
    """Build the database for `date` (default: latest) and make it the live one.

    Returns the live database path. With `limit`, releases are streamed and only
    the first `limit` are parsed (dev runs); such a build will not replace a full
    database unless `force` is set. `opts` picks the variant (default trimmed) and
    the number of cores for parsing releases; with more than one core the other
    files are parsed alongside releases.
    """
    opts = opts or IngestOptions()
    db_dir.mkdir(parents=True, exist_ok=True)
    db_date = date or source.latest_date()
    if not re.fullmatch(r"\d{8}", db_date):
        raise UpdateError(f"database date must be YYYYMMDD, got {db_date[:30]!r}")
    live = live_snapshot(db_dir)
    live_meta = read_meta(live) if live else None

    if live_meta and not force:
        live_complete = live_meta["release_limit"] is None
        if limit is not None and live_complete:
            raise UpdateError(f"{live.name} is a complete database; a --limit build would replace it "
                              "(use --force, or --db-path for a separate directory)")
        if (limit is None and live_complete and live_meta["db_date"] == db_date
                and live_meta.get("schema_version") == SCHEMA_VERSION
                and live_meta["variant"] == opts.variant):
            log.info("database already at version %s (%s): %s", db_date, opts.variant, live)
            return live

    log.info("building %s database version %s in %s", opts.variant, db_date, db_dir)
    kinds = opts.kinds
    timings: dict[str, float] = {}
    checksums = {k: v for k, v in source.checksums(db_date).items()
                 if k in {dated_filename(db_date, kind) for kind in kinds}}
    downloads = db_dir / "downloads"
    staging = db_dir / "staging"
    downloads.mkdir(exist_ok=True)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()

    t0 = time.perf_counter()
    stream_releases = limit is not None
    for kind in kinds:
        if kind == "releases" and stream_releases:
            continue
        name = dated_filename(db_date, kind)
        dest = downloads / name
        if dest.exists() and sha256_file(dest) == checksums[name]:
            log.info("using existing download %s", dest)
            continue
        source.download(name, dest, checksums[name])
    timings["download"] = round(time.perf_counter() - t0, 1)

    others = tuple(k for k in kinds if k != "releases")
    side = None
    if opts.cores > 1:
        side = ProcessPoolExecutor(1, mp_context=mp.get_context("spawn"))
        others_done = side.submit(_parse_downloaded_in_worker, others, downloads, db_date, staging, opts)
    else:
        timings.update(_parse_downloaded(others, downloads, db_date, staging, opts))
    try:
        t0 = time.perf_counter()
        name = dated_filename(db_date, "releases")
        if stream_releases:
            log.info("streaming %s (first %d releases)", name, limit)
            with source.open(name) as raw:
                _parse_file("releases", raw, staging, None, limit, opts)
        else:
            path = downloads / name
            with open(path, "rb") as raw:
                _parse_file("releases", raw, staging, path.stat().st_size, None, opts)
        timings["parse_releases"] = round(time.perf_counter() - t0, 1)
        if side:
            timings.update(others_done.result())
    finally:
        if side:
            side.shutdown(cancel_futures=True)

    final = db_dir / f"discogs_{db_date}.duckdb"
    tmp = final.with_name(final.name + ".tmp")
    counts = build_snapshot(staging, tmp, db_date, checksums, timings, release_limit=limit,
                            memory_limit=memory_limit, opts=opts)

    if live_meta and live_meta["release_limit"] is None and limit is None:
        if live_meta["variant"] != opts.variant:
            log.info("skipped the row count check: %s is a %s database, this build is %s",
                     live.name, live_meta["variant"], opts.variant)
        elif shrunk := shrunken_tables(counts, live_meta["row_counts"]):
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
    log.info("live database: %s (%s, %d releases)", final, opts.variant, counts["releases"])
    return final
