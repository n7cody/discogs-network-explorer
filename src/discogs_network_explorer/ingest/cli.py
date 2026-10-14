"""`dne-db-update`: build or refresh the local database from the latest Discogs database version."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .download import BASE_URL, ChecksumError, HttpSource
from .parse import VARIANTS, IngestOptions, default_cores
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
    p = argparse.ArgumentParser(prog="dne-db-update", description=__doc__)
    p.add_argument("--db-path", type=Path, default=None,
                   help="Database directory (default: $DNE_DB_PATH or ~/.dne/discogs_db)")
    p.add_argument("--date", metavar="YYYYMMDD", help="database date (default: latest complete)")
    p.add_argument("--force", action="store_true", help="rebuild even if already current")
    p.add_argument("--keep-downloads", action="store_true", help="keep the .xml.gz files")
    p.add_argument("--limit", type=_positive_int, metavar="N",
                   help="dev run: stream releases and stop after N (no releases download)")
    p.add_argument("--memory-limit", metavar="SIZE", help="DuckDB memory limit, e.g. 8GB")
    p.add_argument("--variant", choices=VARIANTS, default="trimmed",
                   help="trimmed (default): original releases only, without data the app never reads; "
                        "full: everything in the Discogs database")
    p.add_argument("--cores", type=_positive_int, metavar="N", default=default_cores(),
                   help=f"processes that parse releases (default: {default_cores()}; 1 parses in this "
                        "process only)")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S", force=True)
    try:
        run_update(args.db_path or default_db_dir(), HttpSource(BASE_URL), date=args.date,
                   force=args.force, keep_downloads=args.keep_downloads, limit=args.limit,
                   memory_limit=args.memory_limit,
                   opts=IngestOptions(variant=args.variant, cores=args.cores))
    except (UpdateError, ChecksumError) as e:
        logging.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
