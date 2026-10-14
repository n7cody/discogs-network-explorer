"""`dne-update`: build or refresh the local database from the latest Discogs Dump."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .download import BASE_URL, ChecksumError, HttpSource
from .update import UpdateError, default_db_dir, run_update


def _positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value[:30]!r}")
    return n


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dne-update", description=__doc__)
    p.add_argument("--db-path", type=Path, default=None,
                   help="Database directory (default: $DNE_DB_PATH or ~/.dne/discogs_db)")
    p.add_argument("--dump", metavar="YYYYMMDD", help="dump date (default: latest complete)")
    p.add_argument("--force", action="store_true", help="rebuild even if already current")
    p.add_argument("--keep-downloads", action="store_true", help="keep the .xml.gz files")
    p.add_argument("--limit", type=_positive_int, metavar="N",
                   help="dev run: stream releases and stop after N (no releases download)")
    p.add_argument("--memory-limit", metavar="SIZE", help="DuckDB memory limit, e.g. 8GB")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S", force=True)
    try:
        run_update(args.db_path or default_db_dir(), HttpSource(BASE_URL), dump=args.dump,
                   force=args.force, keep_downloads=args.keep_downloads, limit=args.limit,
                   memory_limit=args.memory_limit)
    except (UpdateError, ChecksumError) as e:
        logging.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
