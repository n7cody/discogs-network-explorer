import io

import pyarrow.parquet as pq
import pytest

from discogs_network_explorer.ingest.parse import parse_xml


def rows(staging, table, **where):
    out = pq.read_table(staging / f"{table}.parquet").to_pylist()
    return [r for r in out if all(r[k] == v for k, v in where.items())]


def test_release_counts(staging):
    assert len(rows(staging, "releases")) == 43
    assert len(rows(staging, "artists")) == 225
    assert len(rows(staging, "labels")) == 31
    assert len(rows(staging, "masters")) == 18


def test_release_fields(staging):
    (r,) = rows(staging, "releases", release_id=1)
    assert r["year"] == 1999
    assert r["released"] == "1999-03-00"
    assert r["master_id"] == 1660109
    assert r["is_main_release"] is True
    assert r["is_unofficial"] is False
    assert r["genres"] == ["Electronic"]


def test_release_without_year(staging):
    (r,) = rows(staging, "releases", release_id=367)
    assert r["year"] is None


def test_unofficial_release(staging):
    (r,) = rows(staging, "releases", release_id=4737)
    assert r["is_unofficial"] is True
    (f,) = rows(staging, "release_formats", release_id=4737)
    assert f["name"] == "Vinyl"
    assert f["qty"] == 1
    assert f["descriptions"] == ['12"', "33 ⅓ RPM", "Unofficial Release"]


def test_partially_unofficial_is_not_unofficial(staging):
    (r,) = rows(staging, "releases", release_id=13032)
    assert r["is_unofficial"] is False


def test_credits_release_and_track_level(staging):
    c = rows(staging, "credits", release_id=9)
    assert len(c) == 12
    assert {"track_seq": None, "artist_id": 74994, "role": "Executive-Producer"} in [
        {k: x[k] for k in ("track_seq", "artist_id", "role")} for x in c]
    assert {"track_seq": 2, "artist_id": 38, "role": "Remix, Arranged By"} in [
        {k: x[k] for k in ("track_seq", "artist_id", "role")} for x in c]


def test_videos_are_youtube_only(staging):
    v = rows(staging, "release_videos")
    assert v
    assert all("youtu" in x["url"] for x in v)


def test_labels_ignore_nested_sublabels(staging):
    (svek,) = rows(staging, "labels", label_id=5)
    assert svek["name"] == "Svek"
    assert svek["parent_label_id"] == 4711
    assert rows(staging, "labels", label_id=2437) == []


def test_master_fields(staging):
    (m,) = rows(staging, "masters", master_id=113)
    assert m["main_release_id"] == 116925
    assert m["year"] == 2002


def test_masterless_release_has_null_master():
    xml = b"""<releases><release id="5"><title>T</title><master_id>0</master_id>
    <released>0</released></release></releases>"""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        parse_xml("releases", io.BytesIO(xml), Path(d))
        (r,) = pq.read_table(Path(d) / "releases.parquet").to_pylist()
    assert r["master_id"] is None
    assert r["is_main_release"] is None
    assert r["year"] is None


def test_limit_stops_early(tmp_path, source_dir):
    import gzip

    with gzip.open(source_dir / "discogs_20261001_releases.xml.gz", "rb") as f:
        n = parse_xml("releases", f, tmp_path, limit=5)
    assert n == 5
    assert pq.read_table(tmp_path / "releases.parquet").num_rows == 5


def test_unknown_kind_rejected(tmp_path):
    with pytest.raises(ValueError):
        parse_xml("tracks", io.BytesIO(b"<x/>"), tmp_path)


def test_out_of_range_numbers_become_null(tmp_path):
    xml = b"""<releases><release id="6"><title>T</title>
    <formats><format name="CD" qty="99999999999999999999" text=""/></formats>
    <labels><label name="L" catno="1" id="99999999999999999999"/></labels>
    <videos><video src="https://www.youtube.com/watch?v=x" duration="4294967296"><title>v</title></video></videos>
    </release></releases>"""
    parse_xml("releases", io.BytesIO(xml), tmp_path)
    assert pq.read_table(tmp_path / "release_formats.parquet").to_pylist()[0]["qty"] is None
    assert pq.read_table(tmp_path / "release_labels.parquet").to_pylist()[0]["label_id"] is None
    assert pq.read_table(tmp_path / "release_videos.parquet").to_pylist()[0]["duration"] is None
