import json

import duckdb
import pytest

from discogs_network_explorer.ingest import cli
from discogs_network_explorer.ingest.build import SCHEMA_VERSION, read_meta
from discogs_network_explorer.ingest.download import HttpSource
from discogs_network_explorer.ingest.parse import IngestOptions
from discogs_network_explorer.ingest.update import UpdateError, default_db_dir, live_snapshot, run_update


@pytest.fixture
def source(download_server):
    return HttpSource(download_server)


FULL = IngestOptions("full")


def test_full_update_builds_and_cleans_up(tmp_path, source):
    path = run_update(tmp_path, source)
    assert path == tmp_path / "discogs_20261001.duckdb"
    assert live_snapshot(tmp_path) == path
    meta = read_meta(path)
    assert meta["db_date"] == "20261001"
    assert meta["release_limit"] is None
    assert meta["variant"] == "trimmed"
    assert set(meta["checksums"]) == {f"discogs_20261001_{k}.xml.gz"
                                      for k in ("labels", "artists", "releases")}
    assert {"download", "parse_releases", "load", "derived"} <= set(meta["timings"])
    assert "parse_masters" not in meta["timings"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["discogs_20261001.duckdb"]


def test_full_variant_keeps_everything(tmp_path, source):
    meta = read_meta(run_update(tmp_path, source, opts=FULL))
    assert meta["variant"] == "full"
    assert set(meta["checksums"]) == {f"discogs_20261001_{k}.xml.gz"
                                      for k in ("labels", "artists", "masters", "releases")}
    assert meta["row_counts"]["releases"] == 43
    assert meta["row_counts"]["masters"] == 18


def test_parallel_update_matches_single_process(tmp_path, source, spawn_safe_main):
    one = read_meta(run_update(tmp_path / "one", source))
    many = read_meta(run_update(tmp_path / "many", source, opts=IngestOptions(cores=2)))
    assert many["row_counts"] == one["row_counts"]
    assert many["ingest_options"]["cores"] == 2
    assert {"parse_labels", "parse_artists", "parse_releases"} <= set(many["timings"])


def test_already_current_is_skipped(tmp_path, source):
    path = run_update(tmp_path, source)
    built_at = read_meta(path)["built_at"]
    assert run_update(tmp_path, source) == path
    assert read_meta(path)["built_at"] == built_at
    run_update(tmp_path, source, force=True)
    assert read_meta(path)["built_at"] > built_at


def test_old_schema_is_rebuilt(tmp_path, source):
    path = run_update(tmp_path, source)
    con = duckdb.connect(str(path))
    con.execute("UPDATE snapshot_meta SET schema_version = 1")
    con.close()
    built_at = read_meta(path)["built_at"]
    assert run_update(tmp_path, source) == path
    meta = read_meta(path)
    assert meta["schema_version"] == SCHEMA_VERSION
    assert meta["built_at"] > built_at


def test_old_snapshot_replaced(tmp_path, source):
    old = tmp_path / "discogs_20260901.duckdb"
    duckdb.connect(str(old)).close()
    run_update(tmp_path, source)
    assert not old.exists()
    assert live_snapshot(tmp_path).name == "discogs_20261001.duckdb"


def test_keep_downloads(tmp_path, source):
    run_update(tmp_path, source, keep_downloads=True)
    assert len(list((tmp_path / "downloads").glob("*.xml.gz"))) == 3


def test_limit_streams_releases(tmp_path, source):
    path = run_update(tmp_path, source, limit=10, keep_downloads=True, opts=FULL)
    meta = read_meta(path)
    assert meta["release_limit"] == 10
    assert meta["row_counts"]["releases"] == 10
    assert not (tmp_path / "downloads" / "discogs_20261001_releases.xml.gz").exists()


def test_limit_will_not_replace_full_snapshot(tmp_path, source):
    run_update(tmp_path, source)
    with pytest.raises(UpdateError):
        run_update(tmp_path, source, limit=10)
    assert read_meta(live_snapshot(tmp_path))["release_limit"] is None


def test_full_run_replaces_limited_snapshot_of_same_date(tmp_path, source):
    run_update(tmp_path, source, limit=10)
    path = run_update(tmp_path, source)
    assert read_meta(path)["release_limit"] is None


def _fake_previous(tmp_path, counts, variant=None):
    """A previous database's meta; variant None stands for one built before variants existed."""
    prev = tmp_path / "discogs_20260901.duckdb"
    con = duckdb.connect(str(prev))
    extra = ", ? AS variant, '{}'::JSON AS ingest_options" if variant else ""
    con.execute("CREATE TABLE snapshot_meta AS SELECT '20260901' AS db_date, now() AS built_at, "
                "'{}'::JSON AS checksums, ?::JSON AS row_counts, '{}'::JSON AS timings, "
                f"NULL::INTEGER AS release_limit, 1 AS schema_version{extra}",
                [json.dumps(counts), *([variant] if variant else [])])
    con.close()
    return prev


def test_shrunken_build_keeps_old_snapshot(tmp_path, source):
    prev = _fake_previous(tmp_path, {"releases": 1000}, variant="trimmed")
    with pytest.raises(UpdateError, match="releases"):
        run_update(tmp_path, source)
    assert live_snapshot(tmp_path) == prev
    assert not list(tmp_path.glob("*.tmp"))


def test_shrunken_full_build_against_pre_variant_database(tmp_path, source):
    prev = _fake_previous(tmp_path, {"releases": 1000})
    with pytest.raises(UpdateError, match="releases"):
        run_update(tmp_path, source, opts=FULL)
    assert live_snapshot(tmp_path) == prev


def test_row_count_check_skipped_between_variants(tmp_path, source):
    # A full database of the same date's size: the trimmed build has far fewer rows and still replaces it
    full_counts = read_meta(run_update(tmp_path / "f", source, opts=FULL))["row_counts"]
    _fake_previous(tmp_path, full_counts)
    assert read_meta(run_update(tmp_path, source))["variant"] == "trimmed"


def test_row_count_check_skipped_from_trimmed_to_full(tmp_path, source):
    _fake_previous(tmp_path, {"releases": 1000, "role_credits": 10_000}, variant="trimmed")
    assert read_meta(run_update(tmp_path, source, opts=FULL))["variant"] == "full"


def test_other_variant_is_rebuilt(tmp_path, source):
    path = run_update(tmp_path, source)
    built_at = read_meta(path)["built_at"]
    assert run_update(tmp_path, source, opts=IngestOptions(cores=1)) == path
    assert read_meta(path)["built_at"] == built_at
    run_update(tmp_path, source, opts=FULL)
    meta = read_meta(path)
    assert meta["variant"] == "full" and meta["built_at"] > built_at
    built_at = meta["built_at"]
    assert run_update(tmp_path, source, opts=FULL) == path
    assert read_meta(path)["built_at"] == built_at
    run_update(tmp_path, source)
    assert read_meta(path)["variant"] == "trimmed"


def test_bad_checksum_aborts(tmp_path, source, monkeypatch):
    real = source.checksums
    monkeypatch.setattr(source, "checksums", lambda d: {**real(d), "discogs_20261001_labels.xml.gz": "0" * 64})
    with pytest.raises(Exception, match="sha256"):
        run_update(tmp_path, source)
    assert live_snapshot(tmp_path) is None


def test_default_db_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DNE_DB_PATH", str(tmp_path))
    assert default_db_dir() == tmp_path
    monkeypatch.delenv("DNE_DB_PATH")
    assert default_db_dir().parts[-2:] == (".dne", "discogs_db")


def test_cli(tmp_path, download_server, monkeypatch, spawn_safe_main):
    monkeypatch.setattr(cli, "BASE_URL", download_server)
    assert cli.main(["--db-path", str(tmp_path), "--date", "20261001", "--limit", "5"]) == 0
    meta = read_meta(live_snapshot(tmp_path))
    assert meta["release_limit"] == 5
    assert meta["variant"] == "trimmed"
    assert meta["ingest_options"]["cores"] == cli.default_cores()
    assert cli.main(["--db-path", str(tmp_path), "--date", "20261001", "--limit", "5", "--cores", "1"]) == 0
    assert cli.main(["--db-path", str(tmp_path), "--date", "20261001", "--cores", "2"]) == 0
    assert cli.main(["--db-path", str(tmp_path), "--date", "20261001", "--limit", "5"]) == 1
    assert cli.main(["--db-path", str(tmp_path), "--date", "20261001", "--variant", "full", "--cores", "1"]) == 0
    meta = read_meta(live_snapshot(tmp_path))
    assert (meta["variant"], meta["ingest_options"]["cores"]) == ("full", 1)


@pytest.mark.parametrize("args", [["--variant", "slim"], ["--cores", "0"], ["--workers", "2"]])
def test_cli_rejects_bad_options(args, tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["--db-path", str(tmp_path), *args])
