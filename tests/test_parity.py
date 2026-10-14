"""Parity of local database release-label links with cached Discogs API responses.

The full comparison reads the requests-cache file (`DNE_PARITY_CACHE`, default
`discogs_cache.sqlite` at the repo root) and the live local database. It reports
differences rather than failing on them; run with `pytest -s` to see the report.
"""

import json
import os
import pickle
import re
import sqlite3
from collections import Counter
from pathlib import Path

import duckdb
import pytest

from discogs_network_explorer.ingest.update import default_db_dir, live_snapshot

CACHE = Path(os.environ.get("DNE_PARITY_CACHE", Path(__file__).parents[1] / "discogs_cache.sqlite"))
LIVE = live_snapshot(default_db_dir())

# Label IDs in the cached API responses for the fixture's "cached API release (parity)" rows
FIXTURE_API_LABELS = {7773: {66}, 10335: {11657}, 55716: {889}, 64933: {621}, 67848: {1263}}


def snapshot_release_labels(db: Path, release_ids) -> dict[int, dict[int, set[str]]]:
    """Label ID -> label names per release in the local database; absent releases are left out."""
    with duckdb.connect(str(db), read_only=True) as con:
        rows = con.execute("""
            SELECT r.release_id, rl.label_id, rl.label_name
            FROM releases r
            LEFT JOIN release_labels rl USING (release_id)
            WHERE r.release_id IN (SELECT unnest($ids))
        """, {"ids": sorted(release_ids)}).fetchall()
    out: dict[int, dict[int, set[str]]] = {}
    for rid, lid, name in rows:
        labels = out.setdefault(rid, {})
        if lid is not None:
            labels.setdefault(lid, set()).add(name)
    return out


def newest_release_id(db: Path) -> int:
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute("SELECT max(release_id) FROM releases").fetchone()[0]


def classify(api: set[int], snap: set[int] | None, newer_than_dump: bool = False) -> str:
    if snap is None:
        return "release added after the Dump" if newer_than_dump else "release missing from local database"
    if api == snap:
        return "match"
    if api < snap:
        return "local database has extra labels"
    if snap < api:
        return "local database lacks API labels"
    return "labels replaced"


def read_cache(path: Path) -> tuple[dict[int, dict], set[int]]:
    """Cached /releases/{id} responses (id -> {labels, names, created_at}) and master-entry IDs."""
    releases: dict[int, dict] = {}
    master_ids: set[int] = set()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for (value,) in con.execute("SELECT value FROM responses"):
            resp = pickle.loads(value)
            if resp["status_code"] != 200:
                continue
            route = resp["url"].removeprefix("https://api.discogs.com").split("?")[0]
            if re.fullmatch(r"/artists/\d+/releases", route):
                body = json.loads(resp["_content"])
                master_ids |= {it["id"] for it in body.get("releases", []) if it.get("type") == "master"}
            elif m := re.fullmatch(r"/releases/(\d+)", route):
                body = json.loads(resp["_content"])
                labels = [lab for lab in body.get("labels") or [] if lab.get("id") is not None]
                releases[int(m.group(1))] = {
                    "labels": {int(lab["id"]) for lab in labels},
                    "names": {int(lab["id"]): lab.get("name") for lab in labels},
                    "created_at": str(resp.get("created_at", ""))[:10],
                }
    finally:
        con.close()
    return releases, master_ids


def test_fixture_release_labels_match_api(snapshot):
    snap = snapshot_release_labels(snapshot, FIXTURE_API_LABELS)
    assert {rid: classify(api, set(snap[rid])) for rid, api in FIXTURE_API_LABELS.items()} == {
        rid: "match" for rid in FIXTURE_API_LABELS}


def compare(cached: dict[int, dict], snap: dict[int, dict[int, set[str]]], newest: int):
    categories = Counter()
    examples: dict[str, list[str]] = {}
    renamed = []
    for rid, api in sorted(cached.items()):
        labels = snap.get(rid)
        cat = classify(api["labels"], set(labels) if labels is not None else None, rid > newest)
        categories[cat] += 1
        if cat != "match" and len(examples.setdefault(cat, [])) < 5:
            examples[cat].append(f"{rid},{' '.join(map(str, sorted(api['labels'])))},"
                                 f"{' '.join(map(str, sorted(labels or [])))},{api['created_at']}")
        if cat == "match":
            renamed += [(rid, lid, api["names"][lid], " / ".join(sorted(labels[lid])))
                        for lid in api["labels"] if api["names"][lid] not in labels[lid]]
    return categories, examples, renamed


@pytest.mark.skipif(LIVE is None or not CACHE.exists(), reason="needs the live local database and the API cache")
def test_live_snapshot_parity_report():
    cached, master_ids = read_cache(CACHE)
    master_bug = {rid: v for rid, v in cached.items() if rid in master_ids}
    compared = {rid: v for rid, v in cached.items() if rid not in master_ids}
    snap = snapshot_release_labels(LIVE, cached)
    newest = newest_release_id(LIVE)
    categories, examples, renamed = compare(compared, snap, newest)
    bug_categories, _, _ = compare(master_bug, snap, newest)

    total = sum(categories.values())
    lines = [
        f"local database: {LIVE.name} (newest release_id {newest})",
        f"cached releases: {len(cached)}; excluded (master bug): {len(master_bug)}; compared: {total}",
        f"match rate: {categories['match'] / total:.2%}",
        "category,releases",
        *(f"{cat},{n}" for cat, n in categories.most_common()),
        f"label name differs on a matching link,{len(renamed)}",
        "",
        "excluded master-bug releases (informational)",
        "category,releases",
        *(f"{cat},{n}" for cat, n in bug_categories.most_common()),
    ]
    for cat, rows in examples.items():
        lines += ["", f"examples: {cat}", "release_id,api_label_ids,snapshot_label_ids,cached_on", *rows]
    if renamed:
        lines += ["", "examples: label name differs", "release_id,label_id,api_name,snapshot_name",
                  *(f"{r},{lid},{a},{s}" for r, lid, a, s in renamed[:5])]
    print("\n" + "\n".join(lines))

    assert total > 0
