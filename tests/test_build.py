import json
import shutil

import duckdb
import pytest

from discogs_network_explorer.ingest.build import SCHEMA_VERSION, read_meta, shrunken_tables


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
    assert one(con, "SELECT count(*) FROM sub_tracks") == (0,)


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
    assert meta["schema_version"] == SCHEMA_VERSION == 3
    assert "sub_tracks" in meta["row_counts"]
    assert meta["variant"] == "full"
    assert meta["ingest_options"]["variant"] == "full"
    assert meta["ingest_options"]["master_rule"] is False
    assert {"credits", "masters", "role_credits"} <= set(meta["row_counts"])


def test_read_meta_missing_file(tmp_path):
    assert read_meta(tmp_path / "nope.duckdb") is None


def test_shrunken_tables():
    prev = {"releases": 100, "artists": 50, "gone": 1}
    assert shrunken_tables({"releases": 96, "artists": 40}, prev) == ["artists"]
    assert shrunken_tables({"releases": 96, "artists": 50}, prev) == []


def test_meta_json_is_valid(con):
    raw = one(con, "SELECT row_counts FROM snapshot_meta")[0]
    assert isinstance(json.loads(raw), dict)


def test_meta_without_variant_reads_as_full(snapshot, tmp_path):
    import shutil

    old = tmp_path / "old.duckdb"
    shutil.copy(snapshot, old)
    with duckdb.connect(str(old)) as c:
        c.execute("ALTER TABLE snapshot_meta DROP COLUMN variant")
        c.execute("ALTER TABLE snapshot_meta DROP COLUMN ingest_options")
    meta = read_meta(old)
    assert meta["variant"] == "full"
    assert meta["ingest_options"] is None


# Trimmed variant

@pytest.fixture(scope="module")
def tcon(snapshot_trimmed):
    c = duckdb.connect(str(snapshot_trimmed), read_only=True)
    yield c
    c.close()


MAIN_ONLY = "release_id IN (SELECT release_id FROM releases WHERE master_id IS NULL OR is_main_release)"


def _set(con, sql):
    return set(con.execute(sql).fetchall())


def test_trimmed_meta(snapshot_trimmed):
    meta = read_meta(snapshot_trimmed)
    assert meta["variant"] == "trimmed"
    assert meta["schema_version"] == SCHEMA_VERSION
    opts = meta["ingest_options"]
    assert opts["master_rule"] and opts["role_credits_only"] and opts["skip_masters"]
    assert "credits" not in meta["row_counts"] and "masters" not in meta["row_counts"]
    assert meta["row_counts"]["releases"] == 37


def test_trimmed_tables(tcon):
    tables = {r[0] for r in tcon.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    assert "credits" not in tables and "masters" not in tables
    assert {"role_credits", "artist_release_roles", "label_stats", "artist_stats"} <= tables
    assert one(tcon, "SELECT count(*) FROM releases WHERE master_id IS NOT NULL AND NOT is_main_release") == (0,)


def test_role_rule_matches_sql_rule(con, tcon):
    # full: role_credits from the credits table in SQL; trimmed: role_credits written by the parser
    sql = f"SELECT release_id, artist_id, role FROM role_credits WHERE {MAIN_ONLY}"
    assert _set(tcon, sql) == _set(con, sql)
    assert _set(tcon, sql)


def test_artist_release_roles_match_full_on_original_releases(con, tcon):
    sql = f"SELECT artist_id, release_id, role FROM artist_release_roles WHERE {MAIN_ONLY}"
    assert _set(tcon, sql) == _set(con, sql)
    assert one(tcon, "SELECT count(*) FROM artist_release_roles") == one(
        con, f"SELECT count(*) FROM artist_release_roles WHERE {MAIN_ONLY}")


def test_trimmed_label_stats_count_original_releases(con, tcon):
    assert one(tcon, "SELECT catalog_size FROM label_stats WHERE label_id = 99137") == one(con, f"""
        SELECT count(DISTINCT release_id) FROM release_labels WHERE label_id = 99137 AND {MAIN_ONLY}""")


def test_trimmed_prunes_unreferenced_artists_and_labels(con, tcon):
    referenced = _set(tcon, """SELECT artist_id FROM release_artists UNION SELECT artist_id FROM track_artists
                               UNION SELECT artist_id FROM role_credits""")
    kept = _set(tcon, "SELECT artist_id FROM artists")
    assert kept <= referenced
    assert kept == referenced & _set(con, "SELECT artist_id FROM artists")
    assert len(kept) < one(con, "SELECT count(*) FROM artists")[0]
    labels = _set(tcon, "SELECT label_id FROM labels")
    on_releases = _set(tcon, "SELECT label_id FROM release_labels")
    assert labels == (on_releases | _set(tcon, "SELECT parent_label_id FROM labels")) & _set(
        con, "SELECT label_id FROM labels")


def test_trimmed_keeps_parent_of_referenced_label(staging_trimmed, tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from discogs_network_explorer.ingest.build import build_snapshot
    from discogs_network_explorer.ingest.parse import IngestOptions

    staged = tmp_path / "staging"
    shutil.copytree(staging_trimmed, staged)
    labels = pq.read_table(staged / "labels.parquet").to_pylist()
    labels += [{"label_id": 900001, "name": "Parent", "parent_label_id": 900002},
               {"label_id": 900002, "name": "Grandparent", "parent_label_id": None},
               {"label_id": 900003, "name": "Unused", "parent_label_id": None}]
    labels = [{**l, "parent_label_id": 900001} if l["label_id"] == 99137 else l for l in labels]
    pq.write_table(pa.Table.from_pylist(labels, schema=pq.read_schema(staged / "labels.parquet")),
                   staged / "labels.parquet")
    db = tmp_path / "t.duckdb"
    build_snapshot(staged, db, "20261001", {}, {}, opts=IngestOptions("trimmed"))
    with duckdb.connect(str(db), read_only=True) as c:
        kept = {r[0] for r in c.execute("SELECT label_id FROM labels WHERE label_id > 900000").fetchall()}
    assert kept == {900001, 900002}


def test_build_reads_part_files(staging_trimmed, tmp_path):
    from discogs_network_explorer.ingest.build import build_snapshot
    from discogs_network_explorer.ingest.parse import IngestOptions

    staged = tmp_path / "staging"
    shutil.copytree(staging_trimmed, staged)
    for table in ("releases", "tracks"):
        (staged / table).mkdir()
        (staged / f"{table}.parquet").rename(staged / table / "part-000.parquet")
    counts = build_snapshot(staged, tmp_path / "p.duckdb", "20261001", {}, {}, opts=IngestOptions("trimmed"))
    assert counts["releases"] == 37
    assert counts["tracks"] == read_meta(tmp_path / "p.duckdb")["row_counts"]["tracks"] > 0
