"""Local database backend: the `backend` interface served from the local DuckDB database.

Queries live in `local_db.sql`. IDs cross the interface as strings, years as
int or None, matching `backend`.
"""

from __future__ import annotations

import re
import threading
from importlib.resources import files
from pathlib import Path
from typing import Iterable

import duckdb

from .backend import build_label_to_artists  # noqa: F401  (shared interface, backend-independent)
from .ingest.update import default_db_dir, live_snapshot


def _load_queries() -> dict[str, str]:
    text = files(__package__).joinpath("local_db.sql").read_text()
    parts = re.split(r"^-- name: (\w+)\s*$", text, flags=re.MULTILINE)
    return {name: sql.strip() for name, sql in zip(parts[1::2], parts[2::2])}


QUERIES = _load_queries()

# Joins against up to this many IDs push an IN filter into the scan, so DuckDB reads only
# the row groups holding those IDs (release-keyed tables are stored in release_id order).
SETTINGS = {"dynamic_or_filter_threshold": 10_000}


# Connection

class SnapshotMissing(RuntimeError):
    pass


_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None
_con_key: tuple[Path, int] | None = None
_pinned: Path | None = None


def use_snapshot(path: Path | str | None) -> None:
    """Read `path` instead of the live database; None returns to the live one."""
    global _pinned
    _pinned = Path(path) if path else None
    reconnect()


def snapshot_path() -> Path | None:
    """The database file queries will read: the pinned one, else the live one."""
    return _pinned or live_snapshot(default_db_dir())


def reconnect() -> None:
    """Close the connection; the next query opens the current database."""
    global _con, _con_key
    with _lock:
        if _con is not None:
            _con.close()
        _con = None
        _con_key = None


def _cursor() -> duckdb.DuckDBPyConnection:
    global _con, _con_key
    path = snapshot_path()
    if path is None or not path.exists():
        raise SnapshotMissing(f"No local database in {default_db_dir()}; run `dne-update`.")
    # A new file (or one replaced in place by dne-update) has a new inode
    key = (path, path.stat().st_ino)
    with _lock:
        if _con is None or _con_key != key:
            if _con is not None:
                _con.close()
            # SET GLOBAL so cursors inherit it; a connect-time config would stop other
            # readers (e.g. ingest.build.read_meta) opening the same file in this process
            _con = duckdb.connect(str(path), read_only=True)
            for name, value in SETTINGS.items():
                _con.execute(f"SET GLOBAL {name} = {value}")
            _con_key = key
        return _con.cursor()


def _rows(query: str, **params) -> list[dict]:
    cur = _cursor()
    try:
        cur.execute(QUERIES[query], params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        cur.close()


def _ids(values: Iterable) -> list[int]:
    """Discogs IDs as integers; anything that is not a positive integer is dropped."""
    out = []
    for v in values:
        s = str(v).strip()
        if s.isdigit():
            out.append(int(s))
    return out


# Seed Labels -> artist pool

def get_master_artist_list(
    label_ids: list[str],
    min_year: int | None = None,
    max_year: int | None = None,
    originals_only: bool = False,
) -> list[str]:
    """Artist IDs credited on Seed Label releases within the Seed Window, in discovery order.

    Main Artists, Producer / Co-producer / Remix credits, and Track Artists on
    Various Artists releases. Releases with no year are kept.
    """
    rows = _rows("seed_pool", seed_label_ids=_ids(label_ids), min_year=min_year or None,
                 max_year=max_year or None, originals_only=originals_only)
    return [r["artist_id"] for r in rows]


# Artist pool -> label rows

def get_master_label_rows(
    artists: Iterable[str],
    max_releases_allowed: int = 50,
    max_releases_per_label: int = 10_000,
    min_year: int | None = None,
    max_year: int | None = None,
    seed_label_ids: list[str] | None = None,
    max_global_releases: int | None = None,
    originals_only: bool = False,
) -> list[dict]:
    """One row per (artist, release, label) for every release credited to the artists.

    All years are returned: the year range and cap arguments are accepted for
    interface compatibility and ignored in local database mode. Not On Label is dropped.
    Row keys as `backend.get_master_label_rows`, except `format` is replaced by
    `format_names` and `format_descriptions`.
    """
    return _rows("label_rows", artist_ids=_ids(artists), originals_only=originals_only)


# Labels and artists

def get_label_stats(label_ids: Iterable[str]) -> list[dict]:
    """label_id, label_name, catalog_size, earliest_year, latest_year, n_artists per label.

    Labels with no releases in the database are left out.
    """
    return _rows("label_stats", label_ids=_ids(label_ids))


def get_artist_stats(artist_ids: Iterable[str]) -> list[dict]:
    """artist_id, earliest_year, latest_year, n_releases over each artist's full history."""
    return _rows("artist_stats", artist_ids=_ids(artist_ids))


def _label_stat(label_id: str, key: str):
    stats = get_label_stats([label_id])
    return stats[0][key] if stats else None


def get_label_release_count(label_id: str) -> int:
    return _label_stat(label_id, "catalog_size") or 0


def get_label_earliest_year(label_id: str) -> int | None:
    return _label_stat(label_id, "earliest_year")


def get_label_latest_year(label_id: str) -> int | None:
    return _label_stat(label_id, "latest_year")


def _name(query: str, param: str, entity_id: str) -> str:
    ids = _ids([entity_id])
    rows = _rows(query, **{param: ids[0]}) if ids else []
    return (rows[0]["name"] if rows else None) or str(entity_id)


def get_label_name(label_id: str) -> str:
    """The label's name, or the ID unchanged when unknown (as `backend`)."""
    return _name("label_name", "label_id", label_id)


def get_artist_name(artist_id: str) -> str:
    """The artist's name, or the ID unchanged when unknown (as `backend`)."""
    return _name("artist_name", "artist_id", artist_id)


# dnx catalogs

def get_catalog_videos(
    entity_type: str,
    entity_id: str,
    max_releases: int = 30,
    min_year: int | None = None,
    max_year: int | None = None,
    max_tracks: int = 400,
    originals_only: bool = False,
) -> list[dict]:
    """A label's or artist's releases with videos and tracklists, newest first.

    Whole releases are added while the playlist stays within `max_tracks` items
    (each release counts its videos, or its tracklist entries when it has none).
    `max_releases` is accepted for interface compatibility and ignored. Releases
    with no year are kept and come last. Same shape as `backend.get_catalog_videos`.
    """
    ids = _ids([entity_id])
    entity = ids[0] if ids else None
    rows = _rows("catalog_videos",
                 label_id=entity if entity_type == "label" else None,
                 artist_id=entity if entity_type == "artist" else None,
                 min_year=min_year or None, max_year=max_year or None,
                 originals_only=originals_only, max_tracks=max_tracks)
    for r in rows:
        r["videos"] = r["videos"] or []
        r["tracklist"] = r["tracklist"] or []
    return rows


# API-only (no HTTP in local database mode)

def enable_http_cache(cache_name: str = "discogs_cache") -> None:
    pass


def clear_http_cache(cache_name: str = "discogs_cache") -> None:
    pass
