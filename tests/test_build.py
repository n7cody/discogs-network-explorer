import json

import duckdb
import pytest

from discogs_network_explorer.ingest.build import read_meta, shrunken_tables


@pytest.fixture(scope="module")
def con(snapshot):
    c = duckdb.connect(str(snapshot), read_only=True)
    yield c
    c.close()


def one(con, sql, *params):
    return con.execute(sql, list(params)).fetchone()


def roles_on(con, release_id):
    return dict(con.execute(
        "SELECT artist_id, role FROM artist_release_roles WHERE release_id = ?", [release_id]).fetchall())


def test_base_tables_loaded(con):
    assert one(con, "SELECT count(*) FROM releases") == (43,)
    assert one(con, "SELECT count(*) FROM artists") == (225,)
    assert one(con, "SELECT count(*) FROM labels") == (31,)
    assert one(con, "SELECT count(*) FROM masters") == (18,)


def test_unofficial_overrides_every_role(con):
    roles = roles_on(con, 4737)
    assert roles
    assert set(roles.values()) == {"Unofficial"}
    assert 2967 in roles


def test_credit_roles_and_precedence(con):
    roles = roles_on(con, 9)
    assert 74994 not in roles  # Executive-Producer only
    assert 99026 not in roles  # Featuring, Vocals; Written-By
    assert roles[32] == "Producer"  # Producer on release beats Remix on a track
    assert roles[38] == "Remix"
    assert roles[669] == "Remix"


def test_bracketed_producer_kept(con):
    assert roles_on(con, 22)[28] == "Producer"


def test_various_excluded(con):
    assert one(con, "SELECT count(*) FROM artist_release_roles WHERE artist_id = 194") == (0,)


def test_role_totals(con):
    got = dict(con.execute("SELECT role, count(*) FROM artist_release_roles GROUP BY role").fetchall())
    assert got == {"Main": 54, "Producer": 56, "Remix": 22, "Track": 18, "Unofficial": 5}


def test_label_stats(con):
    assert one(con, """SELECT catalog_size, earliest_year, latest_year, n_artists
                       FROM label_stats WHERE label_id = 99137""") == (9, 2007, 2009, 22)


def test_artist_stats(con):
    row = one(con, "SELECT earliest_year, latest_year, n_releases FROM artist_stats WHERE artist_id = 2967")
    assert row == (1988, 1988, 1)


def test_snapshot_meta(snapshot):
    meta = read_meta(snapshot)
    assert meta["db_date"] == "20261001"
    assert meta["row_counts"]["releases"] == 43
    assert meta["row_counts"]["artist_release_roles"] == 155
    assert meta["checksums"] == {"x": "y"}
    assert meta["timings"]["parse"] == 1.0
    assert "load" in meta["timings"]
    assert meta["release_limit"] is None
    assert meta["schema_version"] >= 1


def test_read_meta_missing_file(tmp_path):
    assert read_meta(tmp_path / "nope.duckdb") is None


def test_shrunken_tables():
    prev = {"releases": 100, "artists": 50, "gone": 1}
    assert shrunken_tables({"releases": 96, "artists": 40}, prev) == ["artists"]
    assert shrunken_tables({"releases": 96, "artists": 50}, prev) == []


def test_meta_json_is_valid(con):
    raw = one(con, "SELECT row_counts FROM snapshot_meta")[0]
    assert isinstance(json.loads(raw), dict)
