"""Load staged Parquet into a DuckDB database and add derived tables."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

import duckdb

from .parse import IngestOptions, schemas

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3
DERIVED_TABLES = ("artist_release_roles", "label_stats", "artist_stats")

# Sort order on load; DuckDB skips row groups by min/max, so sorted keys make lookups fast
LOAD_ORDER = {
    "releases": "release_id",
    "release_labels": "label_id, release_id",
    "artists": "artist_id",
    "labels": "label_id",
    "masters": "master_id",
}

# Full builds keep every credit; role_credits holds the ones the app uses, by the same rule as
# parse.base_roles (trimmed builds write role_credits at parse time)
ROLE_CREDITS_SQL = r"""
CREATE TABLE role_credits AS
SELECT DISTINCT release_id,
       artist_id,
       CASE base_role WHEN 'producer' THEN 'Producer' WHEN 'co-producer' THEN 'Co-producer' ELSE 'Remix' END AS role
FROM (
    SELECT release_id,
           artist_id,
           lower(trim(unnest(string_split(regexp_replace(role, '\s*\[[^\]]*\]', '', 'g'), ',')))) AS base_role
    FROM credits
    WHERE artist_id IS NOT NULL
) AS c
WHERE base_role IN ('producer', 'co-producer', 'remix')
ORDER BY release_id, artist_id, role
"""

# Trimmed builds keep only artists and labels that releases refer to (and parents of those labels)
LOAD_FILTERS = {
    "artists": """
        WHERE artist_id IN (SELECT artist_id FROM release_artists
                            UNION SELECT artist_id FROM track_artists
                            UNION SELECT artist_id FROM role_credits)""",
    "labels": """
        WHERE label_id IN (
            WITH RECURSIVE kept(label_id) AS (
                SELECT label_id FROM release_labels WHERE label_id IS NOT NULL
                UNION
                SELECT p.parent_label_id
                FROM read_parquet({src}) AS p
                JOIN kept k ON p.label_id = k.label_id
                WHERE p.parent_label_id IS NOT NULL
            )
            SELECT label_id FROM kept)""",
}


def _parquet_source(staging: Path, table: str) -> str:
    """A read_parquet argument for one table: its single file, or its part files."""
    path = staging / f"{table}.parquet"
    if not path.exists() and (staging / table).is_dir():
        path = staging / table / "*.parquet"
    return "'" + str(path).replace("'", "''") + "'"


def build_snapshot(staging: Path, db_path: Path, db_date: str, checksums: dict[str, str],
                   timings: dict[str, float], release_limit: int | None = None,
                   memory_limit: str | None = None, opts: IngestOptions | None = None) -> dict[str, int]:
    """Create `db_path` from the Parquet files in `staging`; return row counts per table."""
    opts = opts or IngestOptions()
    db_path.unlink(missing_ok=True)
    tmp_dir = staging / "duckdb_tmp"
    config = {"temp_directory": str(tmp_dir)}
    if memory_limit:
        config["memory_limit"] = memory_limit
    con = duckdb.connect(str(db_path), config=config)
    try:
        t0 = time.perf_counter()
        tables = schemas(opts)
        for table in tables:
            src = _parquet_source(staging, table)
            where = LOAD_FILTERS.get(table, "").format(src=src) if opts.trimmed else ""
            order = f" ORDER BY {LOAD_ORDER[table]}" if table in LOAD_ORDER else ""
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet({src}){where}{order}")
            log.info("loaded %s", table)
        if not opts.trimmed:
            con.execute(ROLE_CREDITS_SQL)
        timings = {**timings, "load": round(time.perf_counter() - t0, 1)}

        t0 = time.perf_counter()
        con.execute(files(__package__).joinpath("derived.sql").read_text())
        timings["derived"] = round(time.perf_counter() - t0, 1)
        log.info("built derived tables")

        counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in (*tables, *({"role_credits"} - set(tables)), *DERIVED_TABLES)}
        con.execute("""
            CREATE TABLE snapshot_meta (
                db_date VARCHAR, built_at TIMESTAMPTZ, checksums JSON, row_counts JSON,
                timings JSON, release_limit INTEGER, schema_version INTEGER, variant VARCHAR,
                ingest_options JSON)""")
        con.execute("INSERT INTO snapshot_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            db_date, datetime.now(timezone.utc), json.dumps(checksums), json.dumps(counts),
            json.dumps(timings), release_limit, SCHEMA_VERSION, opts.variant,
            json.dumps(opts.as_json())])
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
    if "dump_date" in meta:  # databases built before the column rename
        meta["db_date"] = meta.pop("dump_date")
    meta.setdefault("variant", "full")  # databases built before variants kept everything
    meta.setdefault("ingest_options", None)
    for k in ("checksums", "row_counts", "timings", "ingest_options"):
        if meta[k] is not None:
            meta[k] = json.loads(meta[k])
    return meta


def shrunken_tables(counts: dict[str, int], previous: dict[str, int], ratio: float = 0.95) -> list[str]:
    """Tables whose new row count fell below `ratio` x the previous database's.

    Only meaningful between databases of the same variant.
    """
    return [t for t, n in counts.items() if t in previous and n < previous[t] * ratio]
