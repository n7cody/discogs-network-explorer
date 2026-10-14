"""Backend selection: the Discogs API (`backend`) or the local database (`local_db`)."""

from __future__ import annotations

from types import ModuleType

from .ingest.update import default_db_dir, live_snapshot

BACKENDS = ("snapshot", "api")

# Functions both backend modules provide
INTERFACE = (
    "get_master_artist_list", "get_master_label_rows", "get_label_release_count",
    "get_label_earliest_year", "get_label_latest_year", "get_label_name", "get_artist_name",
    "get_catalog_videos", "build_label_to_artists", "enable_http_cache", "clear_http_cache",
)


def get_backend(name: str) -> ModuleType:
    """The module serving the shared interface for `name` ("snapshot" or "api")."""
    if name == "snapshot":
        from . import local_db
        return local_db
    if name == "api":
        from . import backend
        return backend
    raise ValueError(f"unknown backend {name!r}; expected one of {BACKENDS}")


def snapshot_available() -> bool:
    """True when a built database exists in the database directory (`DNE_DB_PATH` honoured)."""
    return live_snapshot(default_db_dir()) is not None
