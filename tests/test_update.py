import json

import duckdb
import pytest

from discogs_network_explorer.ingest import cli
from discogs_network_explorer.ingest.build import read_meta
from discogs_network_explorer.ingest.download import HttpSource
from discogs_network_explorer.ingest.update import UpdateError, default_db_dir, live_snapshot, run_update


@pytest.fixture
def source(download_server):
    return HttpSource(download_server)


def test_full_update_builds_and_cleans_up(tmp_path, source):
    path = run_update(tmp_path, source)
    assert path == tmp_path / "discogs_20261001.duckdb"
    assert live_snapshot(tmp_path) == path
    meta = read_meta(path)
    assert meta["db_date"] == "20261001"
    assert meta["release_limit"] is None
    assert set(meta["checksums"]) == {f"discogs_20261001_{k}.xml.gz"
                                      for k in ("labels", "artists", "masters", "releases")}
    assert {"download", "parse_releases", "load", "derived"} <= set(meta["timings"])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["discogs_20261001.duckdb"]


def test_already_current_is_skipped(tmp_path, source):
    path = run_update(tmp_path, source)
    built_at = read_meta(path)["built_at"]
    assert run_update(tmp_path, source) == path
    assert read_meta(path)["built_at"] == built_at
    run_update(tmp_path, source, force=True)
    assert read_meta(path)["built_at"] > built_at


def test_old_snapshot_replaced(tmp_path, source):
    old = tmp_path / "discogs_20260901.duckdb"
    duckdb.connect(str(old)).close()
    run_update(tmp_path, source)
    assert not old.exists()
    assert live_snapshot(tmp_path).name == "discogs_20261001.duckdb"


def test_keep_downloads(tmp_path, source):
    run_update(tmp_path, source, keep_downloads=True)
    assert len(list((tmp_path / "downloads").glob("*.xml.gz"))) == 4


def test_limit_streams_releases(tmp_path, source):
    path = run_update(tmp_path, source, limit=10, keep_downloads=True)
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


def _fake_previous(tmp_path, counts):
    prev = tmp_path / "discogs_20260901.duckdb"
    con = duckdb.connect(str(prev))
    con.execute("CREATE TABLE snapshot_meta AS SELECT '20260901' AS db_date, now() AS built_at, "
                "'{}'::JSON AS checksums, ?::JSON AS row_counts, '{}'::JSON AS timings, "
                "NULL::INTEGER AS release_limit, 1 AS schema_version", [json.dumps(counts)])
    con.close()
    return prev


def test_shrunken_build_keeps_old_snapshot(tmp_path, source):
    prev = _fake_previous(tmp_path, {"releases": 1000})
    with pytest.raises(UpdateError, match="releases"):
        run_update(tmp_path, source)
    assert live_snapshot(tmp_path) == prev
    assert not list(tmp_path.glob("*.tmp"))


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


def test_cli(tmp_path, download_server, monkeypatch):
    monkeypatch.setattr(cli, "BASE_URL", download_server)
    assert cli.main(["--db-path", str(tmp_path), "--date", "20261001", "--limit", "5"]) == 0
    assert read_meta(live_snapshot(tmp_path))["release_limit"] == 5
    assert cli.main(["--db-path", str(tmp_path), "--date", "20261001", "--limit", "5"]) == 0
    assert cli.main(["--db-path", str(tmp_path), "--date", "20261001"]) == 0
    assert cli.main(["--db-path", str(tmp_path), "--date", "20261001", "--limit", "5"]) == 1
