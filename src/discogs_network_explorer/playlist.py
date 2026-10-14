"""dnx playlist fill: one playlist mixed evenly across the input labels and artists."""

from __future__ import annotations

from itertools import zip_longest

YOUTUBE_MAX_ITEMS = 400
APPLE_MAX_ITEMS = 9999


def fill_limit(platform: str) -> int:
    """Playlist item limit: YouTube is held to 400; Apple Music and Apple+YT fill to 9999."""
    return YOUTUBE_MAX_ITEMS if platform == "YouTube" else APPLE_MAX_ITEMS


def _items(release: dict) -> int:
    """Playlist items a release brings: its videos, or its tracklist entries when it has none."""
    return len(release.get("videos") or []) or len(release.get("tracklist") or [])


def _newest_first(catalog: list[dict]) -> list[dict]:
    return sorted(catalog, key=lambda r: (r.get("year") is None, -(r.get("year") or 0)))


def dedupe_releases(catalogs: list[list[dict]]) -> list[list[dict]]:
    """One release per release ID and per master across all catalogs.

    The kept release lists the release IDs it stands for in `merged_release_ids`.
    Preference: has videos, then more tracks, then newer year, then lower release ID;
    remaining ties keep catalog order.
    """
    def group_key(rel: dict) -> tuple:
        return ("master", rel["master_id"]) if rel.get("master_id") else ("id", str(rel["release_id"]))

    def rank(rel: dict) -> tuple:
        rid = str(rel["release_id"])
        return (not rel.get("videos"), -len(rel.get("tracklist") or []), -(rel.get("year") or 0),
                int(rid) if rid.isdigit() else 0)

    groups: dict[tuple, list[tuple[int, int]]] = {}
    for ci, catalog in enumerate(catalogs):
        for ri, rel in enumerate(catalog):
            groups.setdefault(group_key(rel), []).append((ci, ri))
    winners: dict[tuple[int, int], list[str]] = {}
    for members in groups.values():
        best = min(members, key=lambda m: rank(catalogs[m[0]][m[1]]))
        best_id = str(catalogs[best[0]][best[1]]["release_id"])
        winners[best] = sorted({str(catalogs[c][r]["release_id"]) for c, r in members} - {best_id})
    return [[{**rel, "merged_release_ids": winners[(ci, ri)]}
             for ri, rel in enumerate(catalog) if (ci, ri) in winners]
            for ci, catalog in enumerate(catalogs)]


def mix_catalogs(catalogs: list[list[dict]], max_items: int = YOUTUBE_MAX_ITEMS) -> list[dict]:
    """Releases taken in turn from each catalog, newest first, while within `max_items` items.

    Stops before the first release that would take the playlist past `max_items`.
    Releases with no year come last within their catalog. Releases are first collapsed to one
    per release ID and master (`dedupe_releases`), so duplicates never count toward the limit.
    """
    catalogs = dedupe_releases(catalogs)
    mixed: list[dict] = []
    total = 0
    for round_ in zip_longest(*(_newest_first(c) for c in catalogs)):
        for release in round_:
            if release is None:
                continue
            total += _items(release)
            if total > max_items:
                return mixed
            mixed.append(release)
    return mixed
