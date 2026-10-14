"""Find, download and verify Discogs database files from data.discogs.com."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import BinaryIO, Iterator
from urllib.parse import quote, unquote

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://data.discogs.com/"
KINDS = ("labels", "artists", "masters", "releases")
CHUNK = 1 << 20
LOG_EVERY_S = 60

_FILE_RE = re.compile(r"discogs_(\d{8})_(\w+)\.(?:xml\.gz|txt)")


class ChecksumError(RuntimeError):
    pass


def dated_filename(db_date: str, kind: str) -> str:
    return f"discogs_{db_date}_{kind}.txt" if kind == "CHECKSUM" else f"discogs_{db_date}_{kind}.xml.gz"


def file_url(base_url: str, filename: str) -> str:
    year = _FILE_RE.search(filename).group(1)[:4]
    return f"{base_url}?download={quote(f'data/{year}/{filename}', safe='')}"


def parse_listing(html: str) -> dict[str, set[str]]:
    """Map each database date in a listing page to the file kinds present."""
    found: dict[str, set[str]] = {}
    for href in re.findall(r'href="([^"]+)"', html):
        m = _FILE_RE.search(unquote(href))
        if m:
            found.setdefault(m.group(1), set()).add(m.group(2))
    return found


def complete_dates(listing: dict[str, set[str]]) -> list[str]:
    """Database dates that have all four kinds and a checksum file, oldest first."""
    need = {*KINDS, "CHECKSUM"}
    return sorted(d for d, kinds in listing.items() if need <= kinds)


def parse_checksums(text: str) -> dict[str, str]:
    """Parse `sha256sum` output into {filename: hex digest}."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1]] = parts[0]
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


class HttpSource:
    """Database files served by data.discogs.com (or a stand-in at `base_url`)."""

    def __init__(self, base_url: str = BASE_URL, today: date | None = None, retries: int = 3,
                 session: requests.Session | None = None):
        self.base_url = base_url
        self.today = today or date.today()
        self.retries = retries
        self.session = session or requests.Session()

    def _get(self, url: str, stream: bool = False) -> requests.Response:
        r = self.session.get(url, stream=stream, timeout=60)
        r.raise_for_status()
        return r

    def latest_date(self) -> str:
        """Newest complete database version, looking at this year's listing then last year's."""
        for year in (self.today.year, self.today.year - 1):
            url = f"{self.base_url}?prefix={quote(f'data/{year}/', safe='')}"
            dates = complete_dates(parse_listing(self._get(url).text))
            if dates:
                return dates[-1]
        raise RuntimeError("no complete Discogs database version found")

    def checksums(self, db_date: str) -> dict[str, str]:
        return parse_checksums(self._get(file_url(self.base_url, dated_filename(db_date, "CHECKSUM"))).text)

    def download(self, filename: str, dest: Path, sha256: str) -> None:
        """Download to `dest`, verifying sha256. Restarts from zero on failure (no Range support)."""
        part = dest.with_name(dest.name + ".part")
        for attempt in range(1, self.retries + 1):
            try:
                self._download_once(filename, part, sha256)
                part.replace(dest)
                return
            except (requests.RequestException, OSError) as e:
                part.unlink(missing_ok=True)
                if attempt == self.retries:
                    raise
                log.warning("download of %s failed (%s); retry %d/%d", filename, e, attempt, self.retries - 1)
                time.sleep(10 * attempt)
            except ChecksumError:
                part.unlink(missing_ok=True)
                raise

    def _download_once(self, filename: str, part: Path, sha256: str) -> None:
        h = hashlib.sha256()
        with self._get(file_url(self.base_url, filename), stream=True) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done, t0, last = 0, time.perf_counter(), time.perf_counter()
            for chunk in r.iter_content(CHUNK):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                now = time.perf_counter()
                if now - last >= LOG_EVERY_S:
                    last = now
                    rate = done / (now - t0)
                    eta = (total - done) / rate if total and rate else 0
                    log.info("%s: %.2f / %.2f GB, %.1f MB/s, ETA %.0f min",
                             filename, done / 1e9, total / 1e9, rate / 1e6, eta / 60)
        if h.hexdigest() != sha256:
            raise ChecksumError(f"{filename}: sha256 {h.hexdigest()} != expected {sha256}")
        log.info("downloaded %s (%.2f GB), checksum ok", filename, done / 1e9)

    @contextmanager
    def open(self, filename: str) -> Iterator[BinaryIO]:
        """Stream a file's raw (still gzipped) bytes without saving it."""
        with self._get(file_url(self.base_url, filename), stream=True) as r:
            yield r.raw
