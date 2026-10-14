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


def mix_catalogs(catalogs: list[list[dict]], max_items: int = YOUTUBE_MAX_ITEMS) -> list[dict]:
    """Releases taken in turn from each catalog, newest first, while within `max_items` items.

    Stops before the first release that would take the playlist past `max_items`.
    Releases with no year come last within their catalog.
    """
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
