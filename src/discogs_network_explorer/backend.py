"""
backend.py — Discogs REST API client layer.

Responsibilities:
  - HTTP request management (retries, rate-limit delay, optional caching)
  - Pagination across multi-page Discogs endpoints
  - Seed-label crawl  → master artist list
  - Artist crawl      → per-release label rows (with enriched metadata)
  - Label-to-artists mapping construction

Design notes:
  - Request headers are built dynamically at call time so that the
    DISCOGS_TOKEN set by the Streamlit UI (after module import) is always
    picked up.  A module-level header constant would always be empty.
  - build_label_to_artists returns dict[str, set[str]] (set-valued) so
    callers can use set operations (intersection, etc.) directly.
  - All HTTP calls go through the module-level _session object, which uses
    a _NoCookieJar to discard every Set-Cookie header silently.  Discogs
    routes through Cloudflare, which sets a __cf_bm bot-management cookie
    on the first response.  When that cookie (fingerprinted for the initial
    anonymous/discovery request context) is carried into later authenticated
    requests, Cloudflare detects a fingerprint mismatch and returns 401.
    Dropping cookies entirely eliminates this failure mode while keeping the
    Discogs Authorization token (sent as a request header, not a cookie).
"""

from __future__ import annotations

import http.cookiejar
import os
import time
from typing import Iterable

import requests


# ─────────────────────────────────────────────────────────────────────────────
# COOKIE-LESS SESSION
# ─────────────────────────────────────────────────────────────────────────────

class _NoCookieJar(http.cookiejar.CookieJar):
    """
    A cookie jar that silently discards every cookie it receives.

    Discogs uses Cloudflare's bot-management layer, which injects a __cf_bm
    cookie on the first response.  If that cookie is carried into subsequent
    authenticated requests, Cloudflare rejects them with 401 because the
    cookie's fingerprint was computed for a different request context.

    By discarding all cookies we get clean, stateless requests every time
    while still benefiting from persistent connection pooling (keep-alive)
    and the requests-cache layer.
    """

    def set_cookie(self, cookie: http.cookiejar.Cookie) -> None:  # type: ignore[override]
        """Accept the cookie object but do not store it."""


# Module-level session used by all _safe_get calls.
# Replaced by a CachedSession in enable_http_cache(); the _NoCookieJar
# is re-attached there so the cookie-suppression contract is preserved
# regardless of whether caching is enabled.
_session: requests.Session = requests.Session()
_session.cookies = _NoCookieJar()  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

API_BASE: str = "https://api.discogs.com"

# Discogs rate limit: 60 authenticated requests/minute.
# A 1.1-second delay keeps throughput at ~54 req/min with headroom.
REQ_DELAY_SECONDS: float = 1.1

# Discogs hard cap on items per page.
PAGE_SIZE: int = 100

# HTTP status codes that warrant an automatic retry.
RETRY_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Maximum retry attempts before raising the last exception.
MAX_RETRIES: int = 5

# Seconds to wait between retry attempts.
RETRY_DELAY_SECONDS: float = 2.0


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_headers() -> dict[str, str]:
    """
    Build request headers at call time.

    Reading DISCOGS_TOKEN here (rather than at module level) ensures that
    tokens entered through the Streamlit UI sidebar are always included,
    even though the token is set to os.environ after this module was first
    imported.

    The Authorization header is only added when a non-empty token is
    present.  Sending "Discogs token=" (empty token) can trigger a 401
    on certain Discogs endpoints that validate malformed auth strings,
    whereas omitting the header entirely results in anonymous access.

    The token is stripped of surrounding whitespace to guard against
    copy-paste artifacts (trailing newlines, spaces) that would produce
    a malformed header value and a rejected token.
    """
    headers: dict[str, str] = {"User-Agent": "DiscogsNetworkExplorer/2.0"}
    token = os.getenv("DISCOGS_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Discogs token={token}"
    return headers


def _safe_get(url: str, params: dict | None = None) -> dict:
    """
    Perform a GET request with automatic retry on transient failures.

    Retries up to MAX_RETRIES times when the server responds with a status
    code in RETRY_STATUS_CODES or when a network-level exception occurs.
    Sleeps RETRY_DELAY_SECONDS between attempts.

    Skips the inter-request rate-limit delay when the response was served
    from a requests-cache session (identified by the `from_cache`
    attribute set by that library).

    Raises the last encountered exception if all retries are exhausted.
    """
    last_exc: Exception | None = None

    for _ in range(MAX_RETRIES):
        try:
            resp = _session.get(
                url,
                headers=_get_headers(),
                params=params,
                timeout=10,
            )

            # 401 / 404 are not transient errors — retrying will never help.
            # Raise immediately so callers can skip the resource rather than
            # burning MAX_RETRIES attempts on a permanently bad URL.
            if resp.status_code == 401:
                raise RuntimeError(
                    "Discogs returned 401 Unauthorized.\n"
                    "Check that your personal access token is correct and "
                    "has not been revoked (discogs.com → Settings → Developers). "
                    "Use the 'Clear cache' button in the sidebar and try again."
                )
            if resp.status_code == 404:
                raise RuntimeError(f"Discogs returned 404 Not Found: {url}")

            if resp.status_code in RETRY_STATUS_CODES:
                time.sleep(RETRY_DELAY_SECONDS)
                continue

            resp.raise_for_status()
            result = resp.json()

            # Honour the rate limit only for live (non-cached) responses.
            if not getattr(resp, "from_cache", False):
                time.sleep(REQ_DELAY_SECONDS)

            return result

        except RuntimeError:
            raise  # propagate 401 and other explicit errors immediately
        except Exception as exc:  # noqa: BLE001 — broad catch for transient retry
            last_exc = exc
            time.sleep(RETRY_DELAY_SECONDS)

    if last_exc:
        raise last_exc
    raise RuntimeError(f"GET failed after {MAX_RETRIES} attempts: {url}")


def _paged(
    url: str,
    params: dict | None = None,
    max_items: int = 10_000,
) -> Iterable[dict]:
    """
    Yield items from a paginated Discogs endpoint.

    Iterates pages until the last page is reached or max_items items have
    been yielded.  The Discogs pagination envelope always contains a
    "pagination" object; item lists appear under endpoint-specific keys
    ("releases", "results", "artists") which are tried in order.
    """
    params = dict(params or {})
    params.setdefault("per_page", PAGE_SIZE)

    fetched = 0
    page = 1

    while True:
        params["page"] = page
        data = _safe_get(url, params=params)

        # Try known result-list keys in priority order.
        items: list[dict] = (
            data.get("releases")
            or data.get("results")
            or data.get("artists")
            or []
        )

        if not items:
            break

        for item in items:
            yield item
            fetched += 1
            if fetched >= max_items:
                return

        pagination = data.get("pagination", {})
        # Coerce to int — some Discogs endpoints return pagination values as
        # strings, which would cause TypeError when compared with >= operator.
        current_page = int(pagination.get("page", page))
        total_pages  = int(pagination.get("pages", 0))
        if current_page >= total_pages:
            break

        page += 1


# ─────────────────────────────────────────────────────────────────────────────
# RELEASE DETAIL LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

def get_label_name(label_id: str) -> str:
    """
    Return the human-readable name for a Discogs label ID.

    Used as a fallback for seed labels that have no rows in the fetched
    dataset (e.g. all their releases fall outside the requested year range)
    and therefore never appear in the label_names mapping built from df_raw.

    Returns the label ID string unchanged if the API call fails.
    """
    try:
        data = _safe_get(f"{API_BASE}/labels/{label_id}")
        return data.get("name") or label_id
    except Exception:
        return label_id


def get_artist_name(artist_id: str) -> str:
    """
    Return the human-readable name for a Discogs artist ID.

    Used to resolve placeholder names (artist_<id>) that arise when an
    artist was discovered only via VA compilation tracklists — in that
    path the artist ID is captured but the name field is not stored in
    the crawl dataset.  With HTTP caching enabled the result is cached
    and subsequent calls for the same ID are free.

    Returns the artist ID string unchanged if the API call fails.
    """
    try:
        data = _safe_get(f"{API_BASE}/artists/{artist_id}")
        return data.get("name") or artist_id
    except Exception:
        return artist_id


def get_label_release_count(label_id: str) -> int:
    """
    Return the total number of releases on a Discogs label.

    Fetches page 1 of /labels/{id}/releases and reads pagination.items.
    Used to pre-screen discovered labels by their global catalog size before
    they accumulate rows in the crawl dataset.  Returns 0 on any API error
    so callers can treat unknown labels as small (fail-open).
    """
    try:
        data = _safe_get(
            f"{API_BASE}/labels/{label_id}/releases",
            params={"per_page": 1, "page": 1},
        )
        return int(data.get("pagination", {}).get("items", 0))
    except Exception:
        return 0


def get_label_latest_year(label_id: str) -> int | None:
    """
    Return the year of the most recent release on a Discogs label.

    Fetches a single release from /labels/{id}/releases sorted by year
    descending.  Returns None on any API error or if no year is available.
    """
    try:
        data = _safe_get(
            f"{API_BASE}/labels/{label_id}/releases",
            params={"per_page": 1, "page": 1,
                    "sort": "year", "sort_order": "desc"},
        )
        releases = data.get("releases") or []
        if releases:
            year = releases[0].get("year")
            if isinstance(year, int) and year > 0:
                return year
        return None
    except Exception:
        return None


def get_label_earliest_year(label_id: str) -> int | None:
    """
    Return the year of the earliest release on a Discogs label.

    Fetches releases from /labels/{id}/releases sorted by year ascending.
    Skips entries with year=0 (no year data on Discogs) and returns the
    first valid year.  Returns None on any API error or if no year is
    available.
    """
    try:
        data = _safe_get(
            f"{API_BASE}/labels/{label_id}/releases",
            params={"per_page": 5, "page": 1,
                    "sort": "year", "sort_order": "asc"},
        )
        for rel in data.get("releases") or []:
            year = rel.get("year")
            if isinstance(year, int) and year > 0:
                return year
        return None
    except Exception:
        return None


def get_release_details(release_id: str) -> dict:
    """
    Fetch full metadata for a single release.

    The artist releases list provides format and role directly, but label
    IDs, genres, styles, and country require this separate call to the
    releases endpoint.

    Returns a dict with keys:
        labels    — list of {"id": str, "name": str}
        genres    — comma-separated genre string, e.g. "Electronic"
        styles    — comma-separated style string, e.g. "Deep House, Techno"
        country   — country string, e.g. "UK"
        year      — int or None
        videos    — list of {"url": str, "title": str} (YouTube links from
                    the Discogs release page, community-curated)
        tracklist — list of {"position": str, "title": str, "artist": str}
                    per-track entries with individual artist names resolved
    """
    data = _safe_get(f"{API_BASE}/releases/{release_id}")

    labels: list[dict] = []
    for lab in (data.get("labels") or []):
        raw_id = lab.get("id")
        if raw_id is None:
            continue
        lid = str(raw_id).strip()
        if lid:
            labels.append({"id": lid, "name": lab.get("name") or "Unknown"})

    year = data.get("year")

    videos: list[dict] = []
    for vid in (data.get("videos") or []):
        uri = (vid.get("uri") or "").strip()
        if "youtube.com/watch" in uri or "youtu.be/" in uri:
            videos.append({"url": uri, "title": vid.get("title") or ""})

    # Build per-track artist + title list for search fallback.
    tracklist: list[dict] = []
    # Top-level artist names (used as fallback when tracks lack per-track artists).
    top_artists = [
        a.get("name", "")
        for a in (data.get("artists") or [])
        if (a.get("name") or "").strip().lower() not in {"various", "various artists"}
    ]
    top_artist_str = ", ".join(top_artists) if top_artists else ""
    for track in (data.get("tracklist") or []):
        track_title = (track.get("title") or "").strip()
        if not track_title:
            continue
        track_artists = [
            a.get("name", "")
            for a in (track.get("artists") or [])
            if (a.get("name") or "").strip().lower() not in {"various", "various artists"}
        ]
        artist_str = ", ".join(track_artists) if track_artists else top_artist_str
        tracklist.append({
            "position": track.get("position") or "",
            "title":    track_title,
            "artist":   artist_str,
        })

    return {
        "labels":    labels,
        "genres":    ", ".join(data.get("genres") or []),
        "styles":    ", ".join(data.get("styles") or []),
        "country":   data.get("country") or "",
        "year":      int(year) if isinstance(year, int) else None,
        "videos":    videos,
        "tracklist": tracklist,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SEED LABELS → MASTER ARTIST LIST
# ─────────────────────────────────────────────────────────────────────────────

def get_master_artist_list(
    label_ids: list[str],
    min_year: int | None = None,
    max_year: int | None = None,
) -> list[str]:
    """
    Crawl seed labels and collect deduplicated artist IDs.

    The /labels/{id}/releases endpoint returns each release with 'artist'
    as a plain concatenated name string — there is no 'artist_id' field on
    the releases list.  To obtain numeric IDs, this function fetches the
    full release detail for each qualifying release and reads the
    structured 'artists' array (which contains integer IDs).

    Only the top-level 'artists' array is used (main credited artists).
    'extraartists' (producers, engineers, remixers) are intentionally
    excluded to keep the seed pool focused on performing artists.

    With HTTP caching enabled, the release detail calls are served from
    cache when get_master_label_rows later encounters the same releases,
    so the extra API cost only applies on the first uncached run.

    Args:
        label_ids: Discogs label IDs to crawl.
        min_year:  Ignore releases released before this year.
        max_year:  Ignore releases released after this year.

    Returns:
        Deduplicated list of artist ID strings in discovery order.
    """
    artists: list[str] = []
    seen: set[str] = set()

    for lid in label_ids:
        url = f"{API_BASE}/labels/{lid}/releases"
        for rel in _paged(url):
            # Apply year window before the more expensive release-detail call.
            year = rel.get("year")
            if min_year and isinstance(year, int) and year < min_year:
                continue
            if max_year and isinstance(year, int) and year > max_year:
                continue

            rid = rel.get("id")
            if not rid:
                continue

            # Fetch the full release record to get the structured artists array.
            # The releases list only provides 'artist' as a plain name string.
            try:
                data = _safe_get(f"{API_BASE}/releases/{rid}")
            except Exception:
                continue

            top_level = data.get("artists") or []
            # Separate real artists from "Various" placeholders.
            real_top = [
                a for a in top_level
                if (a.get("name") or "").strip().lower()
                not in {"various", "various artists"}
            ]

            if real_top:
                # Normal or split release: use top-level credited artists.
                artist_entries = real_top
            else:
                # VA compilation: every top-level credit is "Various".
                # Fall back to per-track artists to discover real contributors.
                # The full tracklist is already present in `data` — no extra
                # API call required.
                track_map: dict[str, dict] = {}
                for track in (data.get("tracklist") or []):
                    for ta in (track.get("artists") or []):
                        ta_id = str(ta.get("id", "")).strip()
                        ta_name = (ta.get("name") or "").strip().lower()
                        if ta_id and ta_name not in {"various", "various artists"}:
                            track_map.setdefault(ta_id, ta)
                artist_entries = list(track_map.values())

            for artist_entry in artist_entries:
                aid_str = str(artist_entry.get("id", "")).strip()
                if aid_str and aid_str not in seen:
                    seen.add(aid_str)
                    artists.append(aid_str)

    return artists


# ─────────────────────────────────────────────────────────────────────────────
# ARTIST SET → MASTER LABEL ROWS
# ─────────────────────────────────────────────────────────────────────────────

def get_master_label_rows(
    artists: Iterable[str],
    max_releases_allowed: int = 50,
    max_releases_per_label: int = 10_000,
    min_year: int | None = None,
    max_year: int | None = None,
    seed_label_ids: list[str] | None = None,
    max_global_releases: int | None = None,
) -> list[dict]:
    """
    Expand a set of artist IDs into one row per (artist, release, label).

    For each artist, pages through their releases.  For each release, calls
    get_release_details to retrieve label IDs (not available on the releases
    list), genres, styles, and country.  The resulting rows are the primary
    data structure consumed by the filter and graph modules.

    Args:
        artists:                Iterable of artist ID strings.
        max_releases_allowed:   Cap on releases fetched per artist.
        max_releases_per_label: Cap on rows contributed by any single
                                discovered (non-seed) label.  Prevents large
                                labels from dominating the dataset.
        min_year:               Skip releases before this year.
        max_year:               Skip releases after this year.
        seed_label_ids:         IDs of seed/input labels.  These are exempt
                                from max_releases_per_label and
                                max_global_releases so they always appear in
                                the dataset and remain connected in the graph.
        max_global_releases:    Exclude any discovered label whose total
                                Discogs catalog (pagination.items from
                                /labels/{id}/releases) exceeds this number.
                                None = no global-size check.  One cached API
                                call is made per newly-encountered label when
                                this is set.

    Returns:
        List of row dicts with keys:
            artist_id, artist_name, release_id, release_title,
            role, format, genres, styles, country, year,
            label_id, label_name
    """
    rows: list[dict] = []
    label_row_count: dict[str, int] = {}
    # Labels that have exceeded max_releases_per_label or max_global_releases.
    # All rows already appended for these labels will be purged at the end so
    # they are fully excluded rather than silently sampled down to the cap.
    excluded_labels: set[str] = set()
    seed_ids_set: set[str] = {str(s) for s in (seed_label_ids or [])}
    # Tracks which labels have already had their global catalog size checked
    # to avoid making duplicate API calls for the same label.
    global_checked: set[str] = set()

    for aid in artists:
        aid = str(aid)
        url = f"{API_BASE}/artists/{aid}/releases"

        # Wrap the paged iterator in a try/except so a 404 or other error
        # on a single artist's releases endpoint skips that artist without
        # aborting the entire crawl.
        try:
            artist_rels = list(_paged(url, max_items=max_releases_allowed))
        except Exception:  # noqa: BLE001
            continue

        for rel in artist_rels:
            rid = rel.get("id")
            if not rid:
                continue

            # Year pre-filter avoids a release-detail API call for releases
            # that are clearly outside the requested year window.
            year_raw = rel.get("year")
            if min_year and isinstance(year_raw, int) and year_raw < min_year:
                continue
            if max_year and isinstance(year_raw, int) and year_raw > max_year:
                continue

            # Fields available directly from the artist releases list.
            artist_name: str = rel.get("artist") or f"artist_{aid}"
            # VA compilations list "Various" as the credited artist on an
            # artist's releases page.  The seed artist (aid) is the real
            # contributor; use their ID as a name fallback and keep the row
            # so that label discovery is not lost.
            if artist_name.strip().lower() in {"various", "various artists"}:
                artist_name = f"artist_{aid}"
            role: str        = rel.get("role") or ""
            fmt: str         = rel.get("format") or ""
            title: str       = rel.get("title") or ""

            # Fetch full release metadata for label IDs and rich fields.
            try:
                details = get_release_details(str(rid))
            except Exception:
                # Non-fatal: skip inaccessible releases rather than aborting.
                continue

            effective_year = details["year"] or (
                int(year_raw) if isinstance(year_raw, int) else None
            )

            for lab in details["labels"]:
                lid = lab["id"]

                if (lab.get("name") or "").startswith("Not On Label"):
                    continue

                # Pre-screen each newly-encountered non-seed label by its
                # global Discogs catalog size.  One cached API call per label.
                if (
                    max_global_releases is not None
                    and lid not in global_checked
                    and lid not in seed_ids_set
                ):
                    global_checked.add(lid)
                    if get_label_release_count(lid) > max_global_releases:
                        excluded_labels.add(lid)

                if lid in excluded_labels:
                    continue

                # Seed labels are exempt from the per-label row cap so they
                # always appear in the dataset and stay connected in the graph.
                if lid not in seed_ids_set:
                    count = label_row_count.get(lid, 0)
                    if count >= max_releases_per_label:
                        # Label has too many rows — mark for full exclusion.
                        # Rows already appended for it will be purged below.
                        excluded_labels.add(lid)
                        continue

                rows.append(
                    {
                        "artist_id":     aid,
                        "artist_name":   artist_name,
                        "release_id":    str(rid),
                        "release_title": title,
                        "role":          role,
                        "format":        fmt,
                        "genres":        details["genres"],
                        "styles":        details["styles"],
                        "country":       details["country"],
                        "year":          effective_year,
                        "label_id":      lid,
                        "label_name":    lab["name"],
                    }
                )
                label_row_count[lid] = label_row_count.get(lid, 0) + 1

    # Remove all rows belonging to labels that exceeded the cap.  These are
    # large ambient labels (e.g. major distributors) whose partial rows would
    # give a misleading low row-count and let them slip into the graph.
    if excluded_labels:
        rows = [r for r in rows if r["label_id"] not in excluded_labels]

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# LABEL → ARTISTS MAPPING
# ─────────────────────────────────────────────────────────────────────────────

def get_catalog_videos(
    entity_type: str,
    entity_id: str,
    max_releases: int = 30,
    min_year: int | None = None,
    max_year: int | None = None,
) -> list[dict]:
    """
    Fetch all releases for a label or artist and extract YouTube video links.

    Unlike the main crawl (which only captures releases associated with
    discovered artists), this fetches the full catalog directly from the
    Discogs label or artist releases endpoint.

    Args:
        entity_type: "label" or "artist".
        entity_id:   Discogs label or artist ID.
        max_releases: Maximum releases to process.
        min_year:     Skip releases before this year.
        max_year:     Skip releases after this year.

    Returns:
        List of dicts with keys:
            release_id, release_title, artist_name, year,
            videos (list of {url, title}), label_name
    """
    if entity_type == "label":
        url = f"{API_BASE}/labels/{entity_id}/releases"
    else:
        url = f"{API_BASE}/artists/{entity_id}/releases"

    results: list[dict] = []

    for rel in _paged(url, max_items=max_releases):
        rid = rel.get("id")
        if not rid:
            continue

        year_raw = rel.get("year")
        if min_year and isinstance(year_raw, int) and year_raw < min_year:
            continue
        if max_year and isinstance(year_raw, int) and year_raw > max_year:
            continue

        try:
            details = get_release_details(str(rid))
        except Exception:
            continue

        effective_year = details["year"] or (
            int(year_raw) if isinstance(year_raw, int) else None
        )

        # Second year check using the more accurate release-detail year.
        if min_year and isinstance(effective_year, int) and effective_year < min_year:
            continue
        if max_year and isinstance(effective_year, int) and effective_year > max_year:
            continue

        artist_name = rel.get("artist") or ""
        title = rel.get("title") or ""

        # For label catalogs, label name comes from the release details.
        label_name = ""
        if entity_type == "label":
            for lab in details["labels"]:
                if lab["id"] == entity_id:
                    label_name = lab["name"]
                    break
            if not label_name and details["labels"]:
                label_name = details["labels"][0]["name"]

        results.append({
            "release_id":    str(rid),
            "release_title": title,
            "artist_name":   artist_name,
            "year":          effective_year,
            "videos":        details["videos"],
            "tracklist":     details["tracklist"],
            "label_name":    label_name,
        })

    return results


def build_label_to_artists(rows: list[dict]) -> dict[str, set[str]]:
    """
    Build a mapping from label_id to the set of unique artist_ids on that label.

    Returns set-valued dict so callers can use set operations (intersection,
    union, issubset) directly without an extra conversion step.
    """
    mapping: dict[str, set[str]] = {}
    for r in rows:
        lid = str(r.get("label_id", "")).strip()
        aid = str(r.get("artist_id", "")).strip()
        if lid and aid:
            mapping.setdefault(lid, set()).add(aid)
    return mapping


# ─────────────────────────────────────────────────────────────────────────────
# OPTIONAL HTTP CACHE
# ─────────────────────────────────────────────────────────────────────────────

def enable_http_cache(cache_name: str = "discogs_cache") -> None:
    """
    Replace the module-level session with a requests-cache CachedSession.

    Caches HTTP 200 responses for 24 hours so that repeated crawls over
    the same releases are served instantly from disk.  Only status code 200
    is cached — error responses (401, 429, 5xx) are never stored, which
    prevents stale error payloads from being replayed on subsequent runs.

    The CachedSession is given a _NoCookieJar so the Cloudflare __cf_bm
    cookie-suppression contract is preserved even with caching active.

    Silently does nothing if requests-cache is not installed.
    """
    global _session  # noqa: PLW0603
    try:
        import requests_cache  # noqa: PLC0415
        cached: requests.Session = requests_cache.CachedSession(
            cache_name,
            expire_after=86_400,
            allowable_codes=(200,),  # never cache error responses
        )
        cached.cookies = _NoCookieJar()  # type: ignore[assignment]
        _session = cached
    except ImportError:
        pass


def clear_http_cache(cache_name: str = "discogs_cache") -> None:
    """
    Remove all entries from the named requests-cache store and reset the
    module-level session to a plain cookie-less requests.Session.

    Resetting the session discards any Cloudflare cookies that may have
    accumulated in-memory since the last cache clear, ensuring the next
    run starts with a clean slate.  Silently does nothing if requests-cache
    is not installed or the cache does not exist.
    """
    global _session  # noqa: PLW0603
    try:
        import requests_cache  # noqa: PLC0415
        cached: requests.Session = requests_cache.CachedSession(
            cache_name,
            allowable_codes=(200,),
        )
        cached.cookies = _NoCookieJar()  # type: ignore[assignment]
        cached.cache.clear()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    # Always reset to a fresh plain session so in-memory cookies are gone.
    _session = requests.Session()
    _session.cookies = _NoCookieJar()  # type: ignore[assignment]
