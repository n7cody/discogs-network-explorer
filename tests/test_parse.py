import io

import pyarrow.parquet as pq
import pytest

from discogs_network_explorer.ingest import parse
from discogs_network_explorer.ingest.parse import IngestOptions, base_roles, iter_release_bytes, parse_xml

FULL = IngestOptions("full")
NON_MAIN = (50606, 116236, 1744568, 1568748)


def rows(staging, table, **where):
    out = pq.read_table(staging / f"{table}.parquet").to_pylist()
    return [r for r in out if all(r[k] == v for k, v in where.items())]


def test_release_counts(staging):
    assert len(rows(staging, "releases")) == 43
    assert len(rows(staging, "artists")) == 225
    assert len(rows(staging, "labels")) == 31
    assert len(rows(staging, "masters")) == 18
    assert rows(staging, "sub_tracks") == []


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
        parse_xml("releases", io.BytesIO(xml), Path(d), opts=FULL)
        (r,) = pq.read_table(Path(d) / "releases.parquet").to_pylist()
    assert r["master_id"] is None
    assert r["is_main_release"] is None
    assert r["year"] is None


def test_sub_tracks_parsed(tmp_path):
    xml = b"""<releases><release id="7"><title>T</title><tracklist>
    <track><position>A</position><title>Heading</title><duration></duration></track>
    <track><position></position><title>Suite</title><duration></duration>
    <sub_tracks><track><position>1a</position><title>Part One</title><duration>3:00</duration></track>
    <track><position>1b</position><title>Part Two</title><duration></duration></track></sub_tracks></track>
    </tracklist></release></releases>"""
    parse_xml("releases", io.BytesIO(xml), tmp_path, opts=FULL)
    tracks = pq.read_table(tmp_path / "tracks.parquet").to_pylist()
    assert [t["seq"] for t in tracks] == [0, 1]
    subs = [tuple(r.values()) for r in pq.read_table(tmp_path / "sub_tracks.parquet").to_pylist()]
    assert subs == [(7, 1, 0, "1a", "Part One", "3:00"), (7, 1, 1, "1b", "Part Two", None)]


def test_limit_stops_early(tmp_path, source_dir):
    import gzip

    with gzip.open(source_dir / "discogs_20261001_releases.xml.gz", "rb") as f:
        n = parse_xml("releases", f, tmp_path, limit=5, opts=FULL)
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
    parse_xml("releases", io.BytesIO(xml), tmp_path, opts=FULL)
    assert pq.read_table(tmp_path / "release_formats.parquet").to_pylist()[0]["qty"] is None
    assert pq.read_table(tmp_path / "release_labels.parquet").to_pylist()[0]["label_id"] is None
    assert pq.read_table(tmp_path / "release_videos.parquet").to_pylist()[0]["duration"] is None


# Trimmed variant

def test_trimmed_keeps_original_releases_only(staging, staging_trimmed):
    full = rows(staging, "releases")
    kept = {r["release_id"] for r in rows(staging_trimmed, "releases")}
    assert kept == {r["release_id"] for r in full if r["master_id"] is None or r["is_main_release"]}
    assert len(kept) == 37  # 16 main releases, 21 without a master
    assert not kept & set(NON_MAIN)
    for table in ("tracks", "release_labels", "release_artists", "track_artists", "role_credits"):
        assert {r["release_id"] for r in rows(staging_trimmed, table)} <= kept


def test_trimmed_tables_and_columns(staging_trimmed):
    names = {p.name for p in staging_trimmed.iterdir()}
    assert "credits.parquet" not in names and "masters.parquet" not in names
    cols = {t: pq.read_schema(staging_trimmed / f"{t}.parquet").names
            for t in ("releases", "release_formats", "release_labels", "release_artists", "release_videos")}
    assert cols["releases"] == ["release_id", "title", "country", "year", "master_id", "is_main_release",
                                "genres", "styles", "is_unofficial"]
    assert cols["release_formats"] == ["release_id", "seq", "name", "descriptions"]
    assert cols["release_labels"] == ["release_id", "label_id", "label_name"]
    assert cols["release_artists"] == ["release_id", "seq", "artist_id", "artist_name"]
    assert cols["release_videos"] == ["release_id", "seq", "url", "title"]


def test_trimmed_role_credits(staging_trimmed):
    rc = rows(staging_trimmed, "role_credits")
    assert rc
    assert {r["role"] for r in rc} <= {"Producer", "Co-producer", "Remix"}
    assert all(r["artist_id"] is not None for r in rc)
    assert len({(r["release_id"], r["artist_id"], r["role"]) for r in rc}) == len(rc)
    # release 9: Producer on the release, Remix on a track; Executive-Producer is not Producer
    on_9 = {(r["artist_id"], r["role"]) for r in rc if r["release_id"] == 9}
    assert {(32, "Producer"), (38, "Remix")} <= on_9
    assert not any(a == 74994 for a, _ in on_9)


def test_base_roles():
    assert base_roles("Producer [Tracks A1, B2], Remix, Written-By") == ["Producer", "Remix"]
    assert base_roles(" co-producer ,Executive-Producer") == ["Co-producer"]
    assert base_roles("\tProducer") == []  # DuckDB trim() strips spaces only
    assert base_roles(None) == [] and base_roles("") == []


def test_trimmed_limit_counts_records_read(tmp_path, source_dir):
    import gzip

    with gzip.open(source_dir / "discogs_20261001_releases.xml.gz", "rb") as f:
        n = parse_xml("releases", f, tmp_path, limit=13)
    assert n == 13
    assert pq.read_table(tmp_path / "releases.parquet").num_rows == 12  # 50606 is non-main


def test_unknown_variant_and_cores_rejected():
    with pytest.raises(ValueError):
        IngestOptions("slim")
    with pytest.raises(ValueError):
        IngestOptions(cores=0)


# Parallel parse

def test_release_bytes_split_across_chunks(monkeypatch, source_dir):
    import gzip

    from lxml import etree

    monkeypatch.setattr(parse, "CHUNK", 97)
    with gzip.open(source_dir / "discogs_20261001_releases.xml.gz", "rb") as f:
        recs = list(iter_release_bytes(f))
    assert len(recs) == 43
    assert [int(etree.fromstring(r).get("id")) for r in recs][:3] == [1, 3, 9]
    assert all(r.startswith(b"<release ") and r.endswith(b"</release>") for r in recs)


def test_non_main_record_prefilter():
    rec = b'<release id="1"><master_id is_main_release="false">%s</master_id></release>'
    assert parse.is_non_main_record(rec % b"113")
    assert not parse.is_non_main_record(rec % b"0")
    assert not parse.is_non_main_record(rec.replace(b"false", b"true") % b"113")
    assert not parse.is_non_main_record(b'<release id="1"><title>T</title></release>')


def _table(path, table):
    t = pq.read_table(path / f"{table}.parquet" if (path / f"{table}.parquet").exists() else path / table)
    return sorted(map(repr, t.to_pylist()))


@pytest.mark.parametrize("variant", ["full", "trimmed"])
def test_parallel_matches_single_process(tmp_path, source_dir, monkeypatch, spawn_safe_main, variant):
    import gzip

    monkeypatch.setattr(parse, "TASK_RECORDS", 3)
    out = {}
    for cores in (1, 3):
        out[cores] = tmp_path / str(cores)
        with gzip.open(source_dir / "discogs_20261001_releases.xml.gz", "rb") as f:
            assert parse_xml("releases", f, out[cores], opts=IngestOptions(variant, cores=cores)) == 43
    assert len(list((out[3] / "releases").glob("part-*.parquet"))) == 3
    for table in parse.tables_for("releases", IngestOptions(variant)):
        assert _table(out[3], table) == _table(out[1], table), table


def test_parallel_limit(tmp_path, source_dir, spawn_safe_main):
    import gzip

    with gzip.open(source_dir / "discogs_20261001_releases.xml.gz", "rb") as f:
        n = parse_xml("releases", f, tmp_path, limit=5, opts=IngestOptions("full", cores=2))
    assert n == 5
    assert pq.read_table(tmp_path / "releases").num_rows == 5


def test_parallel_worker_error_is_raised(tmp_path, spawn_safe_main):
    xml = b'<releases><release id="x"><title>T</title></release></releases>'
    with pytest.raises(RuntimeError, match="worker failed"):
        parse_xml("releases", io.BytesIO(xml), tmp_path, opts=IngestOptions("full", cores=2))
