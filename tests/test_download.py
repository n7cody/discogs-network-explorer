import gzip
from datetime import date

import pytest

from discogs_network_explorer.ingest.download import (
    ChecksumError, HttpSource, complete_dates, file_url, parse_checksums, parse_listing, sha256_file,
)

LISTING = """<pre>
<a href="?prefix=data%2F">../</a>
<a href="?download=data%2F2026%2Fdiscogs_20260901_CHECKSUM.txt">discogs_20260901_CHECKSUM.txt</a>
<a href="?download=data%2F2026%2Fdiscogs_20260901_artists.xml.gz">x</a>
<a href="?download=data%2F2026%2Fdiscogs_20260901_labels.xml.gz">x</a>
<a href="?download=data%2F2026%2Fdiscogs_20260901_masters.xml.gz">x</a>
<a href="?download=data%2F2026%2Fdiscogs_20260901_releases.xml.gz">x</a>
<a href="?download=data%2F2026%2Fdiscogs_20261001_artists.xml.gz">x</a>
<a href="?download=data%2F2026%2Fdiscogs_20261001_labels.xml.gz">x</a>
</pre>"""


def test_parse_listing():
    got = parse_listing(LISTING)
    assert got["20260901"] == {"CHECKSUM", "artists", "labels", "masters", "releases"}
    assert got["20261001"] == {"artists", "labels"}


def test_incomplete_date_is_skipped():
    assert complete_dates(parse_listing(LISTING)) == ["20260901"]


def test_parse_checksums():
    text = "abc123 discogs_20261001_labels.xml.gz\ndef456  discogs_20261001_artists.xml.gz\n\n"
    assert parse_checksums(text) == {"discogs_20261001_labels.xml.gz": "abc123",
                                     "discogs_20261001_artists.xml.gz": "def456"}


def test_file_url():
    assert file_url("https://data.discogs.com/", "discogs_20261001_releases.xml.gz") == (
        "https://data.discogs.com/?download=data%2F2026%2Fdiscogs_20261001_releases.xml.gz")


def test_latest_date(download_server):
    assert HttpSource(download_server, today=date(2026, 10, 13)).latest_date() == "20261001"


def test_latest_date_falls_back_to_previous_year(download_server):
    assert HttpSource(download_server, today=date(2027, 1, 2)).latest_date() == "20261001"


def test_checksums(download_server):
    sums = HttpSource(download_server).checksums("20261001")
    assert set(sums) == {f"discogs_20261001_{k}.xml.gz" for k in ("labels", "artists", "masters", "releases")}


def test_download_verifies_checksum(download_server, source_dir, tmp_path):
    name = "discogs_20261001_labels.xml.gz"
    src = HttpSource(download_server)
    dest = tmp_path / name
    src.download(name, dest, sha256_file(source_dir / name))
    assert dest.read_bytes() == (source_dir / name).read_bytes()
    assert not dest.with_name(name + ".part").exists()


def test_download_bad_checksum_leaves_no_file(download_server, tmp_path):
    name = "discogs_20261001_labels.xml.gz"
    with pytest.raises(ChecksumError):
        HttpSource(download_server).download(name, tmp_path / name, "0" * 64)
    assert list(tmp_path.iterdir()) == []


def test_open_stream(download_server):
    with HttpSource(download_server).open("discogs_20261001_labels.xml.gz") as raw:
        head = gzip.GzipFile(fileobj=raw).read(8)
    assert head == b"<labels>"
