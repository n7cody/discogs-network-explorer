"""Load staged Parquet into a DuckDB database and add derived tables."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

import duckdb

from .parse import SCHEMAS

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DERIVED_TABLES = ("artist_release_roles", "label_stats", "artist_stats")

# Sort order on load; DuckDB skips row groups by min/max, so sorted keys make lookups fast
LOAD_ORDER = {
    "releases": "release_id",
    "release_labels": "label_id, release_id",
    "artists": "artist_id",
    "labels": "label_id",
    "masters": "master_id",
}


def build_snapshot(staging: Path, db_path: Path, dump_date: str, checksums: dict[str, str],
                   timings: dict[str, float], release_limit: int | None = None,
                   memory_limit: str | None = None) -> dict[str, int]:
    """Create `db_path` from `<staging>/<table>.parquet`; return row counts per table."""
    db_path.unlink(missing_ok=True)
    tmp_dir = staging / "duckdb_tmp"
    config = {"temp_directory": str(tmp_dir)}
    if memory_limit:
        config["memory_limit"] = memory_limit
    con = duckdb.connect(str(db_path), config=config)
    try:
        t0 = time.perf_counter()
        for table in SCHEMAS:
            src = str(staging / f"{table}.parquet").replace("'", "''")
            order = f" ORDER BY {LOAD_ORDER[table]}" if table in LOAD_ORDER else ""
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet('{src}'){order}")
            log.info("loaded %s", table)
        timings = {**timings, "load": round(time.perf_counter() - t0, 1)}

        t0 = time.perf_counter()
        con.execute(files(__package__).joinpath("derived.sql").read_text())
        timings["derived"] = round(time.perf_counter() - t0, 1)
        log.info("built derived tables")

        counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in (*SCHEMAS, *DERIVED_TABLES)}
        con.execute("""
            CREATE TABLE snapshot_meta (
                dump_date VARCHAR, built_at TIMESTAMPTZ, checksums JSON, row_counts JSON,
                timings JSON, release_limit INTEGER, schema_version INTEGER)""")
        con.execute("INSERT INTO snapshot_meta VALUES (?, ?, ?, ?, ?, ?, ?)", [
            dump_date, datetime.now(timezone.utc), json.dumps(checksums), json.dumps(counts),
            json.dumps(timings), release_limit, SCHEMA_VERSION])
        con.execute("CHECKPOINT")
    finally:
        con.close()
    return counts


def read_meta(db_path: Path) -> dict | None:
    """Return the database's `snapshot_meta` row as a dict, or None if unreadable."""
    if not db_path.exists():
        return None
    try:
        con = duckdb.connect(str(db_path), read_only=True)
    except duckdb.Error:
        return None
    try:
        cur = con.execute("SELECT * FROM snapshot_meta")
        row = cur.fetchone()
        cols = [d[0] for d in cur.description]
    except duckdb.Error:
        return None
    finally:
        con.close()
    meta = dict(zip(cols, row))
    for k in ("checksums", "row_counts", "timings"):
        meta[k] = json.loads(meta[k])
    return meta


def shrunken_tables(counts: dict[str, int], previous: dict[str, int], ratio: float = 0.95) -> list[str]:
    """Tables whose new row count fell below `ratio` x the previous database's."""
    return [t for t, n in counts.items() if t in previous and n < previous[t] * ratio]
