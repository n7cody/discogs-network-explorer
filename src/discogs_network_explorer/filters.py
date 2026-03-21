"""
filters.py — DataFrame filtering utilities for Discogs release/label data.

All functions accept a DataFrame produced by backend.get_master_label_rows
and return a filtered copy.  Columns assumed present are documented per
function; missing columns are handled gracefully (no-op or empty return).

Filter categories:
  Generic row filters  — year range, format, country, role, genres, styles
  Label-size filter    — keep labels whose row count is within [min, max]
  Summary utility      — unique_labels aggregation
  Mode-specific        — overlap logic for the three seed modes (A / B / C)
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# GENERIC ROW FILTERS
# ─────────────────────────────────────────────────────────────────────────────

def filter_by_year_range(
    df: pd.DataFrame,
    min_year: int | None = None,
    max_year: int | None = None,
) -> pd.DataFrame:
    """
    Keep rows whose 'year' value falls within [min_year, max_year].

    Rows with a null/zero year are retained by default — unknown release
    dates should not silently remove data.  Pass min_year=1 to exclude
    year-0 placeholder entries if desired.
    """
    if "year" not in df.columns or (min_year is None and max_year is None):
        return df

    out = df.copy()
    out["year"] = pd.to_numeric(out["year"], errors="coerce")

    # Build a boolean mask that is True for rows to KEEP.
    # Null years evaluate False in comparisons, so we OR with isnull()
    # to preserve them.
    keep = pd.Series(True, index=out.index)
    null_year = out["year"].isnull()

    if min_year is not None:
        keep &= (out["year"] >= int(min_year)) | null_year
    if max_year is not None:
        keep &= (out["year"] <= int(max_year)) | null_year

    return out[keep].copy()


def filter_by_format(
    df: pd.DataFrame,
    formats: set[str],
) -> pd.DataFrame:
    """
    Keep rows whose 'format' value is in the provided set.

    Comparison is case-insensitive.  An empty formats set is treated as
    "no restriction" and the DataFrame is returned unchanged.
    """
    if not formats or "format" not in df.columns:
        return df

    lower = {f.lower() for f in formats}
    mask = df["format"].fillna("").str.lower().isin(lower)
    return df[mask].copy()


def filter_by_country(
    df: pd.DataFrame,
    countries: set[str],
) -> pd.DataFrame:
    """
    Keep rows whose 'country' value is in the provided set.

    Comparison is case-insensitive.  Empty set = no restriction.
    """
    if not countries or "country" not in df.columns:
        return df

    lower = {c.lower() for c in countries}
    mask = df["country"].fillna("").str.lower().isin(lower)
    return df[mask].copy()


def filter_by_role(
    df: pd.DataFrame,
    roles: set[str],
) -> pd.DataFrame:
    """
    Keep rows whose 'role' value is in the provided set.

    Common Discogs role values: "Main", "Appearance", "TrackAppearance",
    "UnofficialRelease".  Comparison is case-insensitive.  Empty = no filter.
    """
    if not roles or "role" not in df.columns:
        return df

    lower = {r.lower() for r in roles}
    mask = df["role"].fillna("").str.lower().isin(lower)
    return df[mask].copy()


def filter_by_genres(
    df: pd.DataFrame,
    genres: set[str],
) -> pd.DataFrame:
    """
    Keep rows that contain at least one of the selected genres.

    The 'genres' column stores a comma-separated string such as
    "Electronic, Rock".  A row is kept if ANY token in that string
    matches any value in the genres set (case-insensitive).
    Empty set = no restriction.
    """
    if not genres or "genres" not in df.columns:
        return df

    lower = {g.lower() for g in genres}

    def _has_genre(cell: str) -> bool:
        return any(tok.strip().lower() in lower for tok in str(cell).split(","))

    mask = df["genres"].fillna("").apply(_has_genre)
    return df[mask].copy()


def filter_by_styles(
    df: pd.DataFrame,
    styles: set[str],
) -> pd.DataFrame:
    """
    Keep rows that contain at least one of the selected styles.

    The 'styles' column stores a comma-separated string such as
    "Deep House, Techno".  Matching is case-insensitive.
    Empty set = no restriction.
    """
    if not styles or "styles" not in df.columns:
        return df

    lower = {s.lower() for s in styles}

    def _has_style(cell: str) -> bool:
        return any(tok.strip().lower() in lower for tok in str(cell).split(","))

    mask = df["styles"].fillna("").apply(_has_style)
    return df[mask].copy()


# ─────────────────────────────────────────────────────────────────────────────
# LABEL-SIZE FILTER
# ─────────────────────────────────────────────────────────────────────────────

def filter_labels_by_size(
    df: pd.DataFrame,
    min_size: int | None = None,
    max_size: int | None = None,
) -> pd.DataFrame:
    """
    Keep labels whose row count in df is within [min_size, max_size].

    The temporary row-count column is dropped before returning so it
    does not propagate to downstream operations.
    """
    if "label_id" not in df.columns or (min_size is None and max_size is None):
        return df

    _TMP = "_label_row_count"
    counts = df.groupby("label_id").size().rename(_TMP)
    out = df.merge(counts, on="label_id", how="left")

    if min_size is not None:
        out = out[out[_TMP] >= int(min_size)]
    if max_size is not None:
        out = out[out[_TMP] <= int(max_size)]

    # Drop the temporary column so it never leaks into downstream steps.
    return out.drop(columns=[_TMP]).copy()


# ─────────────────────────────────────────────────────────────────────────────
# LABEL SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

def unique_labels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return a summary DataFrame of unique labels with their row counts.

    Columns: label_id, label_name, n (sorted descending by n).
    """
    if "label_id" not in df.columns:
        return df.head(0)

    return (
        df.groupby(["label_id", "label_name"])
        .size()
        .rename("n")
        .reset_index()
        .sort_values("n", ascending=False)
        .reset_index(drop=True)
    )


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _label_to_artists_from_df(df: pd.DataFrame) -> dict[str, set[str]]:
    """
    Build {label_id: set(artist_id)} from a DataFrame with those columns.
    """
    if "label_id" not in df.columns or "artist_id" not in df.columns:
        return {}

    tmp = (
        df[["label_id", "artist_id"]]
        .dropna(subset=["label_id", "artist_id"])
        .astype(str)
    )
    return (
        tmp.groupby("label_id")["artist_id"]
        .apply(lambda s: set(s.tolist()))
        .to_dict()
    )


# ─────────────────────────────────────────────────────────────────────────────
# MODE A — LABELS ONLY
# ─────────────────────────────────────────────────────────────────────────────

def apply_label_only_mode(
    df: pd.DataFrame,
    seed_label_ids: Iterable[str],
    min_overlaps_required: int = 2,
    strict_per_label: bool = False,
    seed_artist_pool: set[str] | None = None,
) -> pd.DataFrame:
    """
    Filter to labels that share artists with the seed label union.

    Union mode (default):
        Keep labels L where |artists(L) ∩ union_seed_artists| >= min_overlaps_required.

    Strict mode (strict_per_label=True):
        Additionally require that for EVERY seed label S with artists,
        |artists(L) ∩ artists(S)| >= 1.  This ensures L has cross-label
        presence across all seed labels, not just one.

    seed_artist_pool:
        The authoritative Phase 1 artist set (all artists discovered from
        seed labels).  Used as the union pool for the overlap check.  If
        not provided, falls back to reconstructing from df rows on seed
        labels (which can miss artists whose seed-label releases were not
        crawled in Phase 2).
    """
    seed_label_ids = [str(x) for x in seed_label_ids if str(x).strip()]
    if not seed_label_ids or df.empty:
        return df

    l2a = _label_to_artists_from_df(df)

    # Use the authoritative Phase 1 pool when available; otherwise
    # reconstruct from whatever seed-label rows exist in the filtered df.
    if seed_artist_pool:
        union_pool = seed_artist_pool
    else:
        seed_pools_union: set[str] = set()
        for lid in seed_label_ids:
            seed_pools_union |= l2a.get(lid, set())
        union_pool = seed_pools_union

    # Per-seed-label pools for strict mode (still from df rows).
    seed_pools: dict[str, set[str]] = {
        lid: l2a.get(lid, set()) for lid in seed_label_ids
    }

    if not union_pool:
        return df

    min_overlaps_required = max(1, int(min_overlaps_required))

    def _keep(label_id: str) -> bool:
        artists = l2a.get(label_id, set())
        if not artists:
            return False
        if len(artists & union_pool) < min_overlaps_required:
            return False
        if strict_per_label:
            for pool in seed_pools.values():
                if pool and not artists & pool:
                    return False
        return True

    return df[df["label_id"].astype(str).map(_keep)].copy()


# ─────────────────────────────────────────────────────────────────────────────
# MODE B — LABELS + ARTISTS
# ─────────────────────────────────────────────────────────────────────────────

def apply_label_plus_artist_mode(
    df: pd.DataFrame,
    seed_label_ids: Iterable[str],
    seed_artist_ids: Iterable[str],
) -> pd.DataFrame:
    """
    Filter to labels that satisfy both artist-presence conditions:

    1. The label's artist set contains ALL explicit seed_artist_ids.
    2. The label's artist set intersects the union of seed-label artist pools
       (at least one artist from any seed label must be present).

    If no seed labels are provided, condition 2 is skipped.
    """
    seed_label_ids  = [str(x) for x in seed_label_ids  if str(x).strip()]
    seed_artist_ids = {str(x) for x in seed_artist_ids if str(x).strip()}

    if df.empty or not seed_artist_ids:
        return df

    l2a = _label_to_artists_from_df(df)
    union_seed_label_artists: set[str] = set()
    for lid in seed_label_ids:
        union_seed_label_artists |= l2a.get(lid, set())

    def _keep(label_id: str) -> bool:
        artists = l2a.get(label_id, set())
        if not artists:
            return False
        if not seed_artist_ids.issubset(artists):
            return False
        if union_seed_label_artists and not artists & union_seed_label_artists:
            return False
        return True

    return df[df["label_id"].astype(str).map(_keep)].copy()


# ─────────────────────────────────────────────────────────────────────────────
# MODE C — ARTISTS ONLY
# ─────────────────────────────────────────────────────────────────────────────

def apply_artists_only_mode(
    df: pd.DataFrame,
    seed_artist_ids: Iterable[str],
    min_overlaps_required: int = 2,
) -> pd.DataFrame:
    """
    Keep labels whose artist set contains at least min_overlaps_required
    of the provided input artist IDs.

    Mirrors apply_label_only_mode: a label is retained when it shares enough
    artists with the input pool, rather than requiring every artist to be
    present (the old strict-subset behaviour).
    """
    seed_artist_ids = {str(x) for x in seed_artist_ids if str(x).strip()}
    if df.empty or not seed_artist_ids:
        return df

    l2a = _label_to_artists_from_df(df)
    min_n = max(1, int(min_overlaps_required))

    def _keep(label_id: str) -> bool:
        return len(seed_artist_ids & l2a.get(label_id, set())) >= min_n

    return df[df["label_id"].astype(str).map(_keep)].copy()


# ─────────────────────────────────────────────────────────────────────────────
# RELEASE ACTIVITY WINDOW FILTERS
# ─────────────────────────────────────────────────────────────────────────────

def filter_labels_by_activity_window(
    df: pd.DataFrame,
    window_start: int,
    window_end: int,
    min_releases_in_window: int = 1,
) -> pd.DataFrame:
    """
    Keep labels that have at least min_releases_in_window releases with a
    year value falling within [window_start, window_end].

    Labels with zero qualifying releases in the window are dropped.
    Labels with null years for all their releases are also dropped, because
    their activity cannot be confirmed within the specified period.
    """
    if "year" not in df.columns or "label_id" not in df.columns:
        return df

    year_col = pd.to_numeric(df["year"], errors="coerce")
    in_window = (year_col >= window_start) & (year_col <= window_end)

    window_counts = (
        df[in_window]
        .groupby("label_id")
        .size()
        .rename("_win_count")
    )
    qualifying = window_counts[window_counts >= max(1, int(min_releases_in_window))].index
    return df[df["label_id"].isin(qualifying)].copy()


def filter_artists_by_activity_window(
    df: pd.DataFrame,
    window_start: int,
    window_end: int,
    min_releases_in_window: int = 1,
) -> pd.DataFrame:
    """
    Keep rows for artists that have at least min_releases_in_window releases
    with a year value within [window_start, window_end].

    Used in artist-seed modes to confirm that each artist in the dataset
    has demonstrated activity within the specified period.  Artists who
    have no qualifying releases are removed entirely — all their rows are
    dropped, which also removes any labels that lose all their artists.
    """
    if "year" not in df.columns or "artist_id" not in df.columns:
        return df

    year_col = pd.to_numeric(df["year"], errors="coerce")
    in_window = (year_col >= window_start) & (year_col <= window_end)

    window_counts = (
        df[in_window]
        .groupby("artist_id")
        .size()
        .rename("_win_count")
    )
    qualifying = window_counts[window_counts >= max(1, int(min_releases_in_window))].index
    return df[df["artist_id"].isin(qualifying)].copy()


# ─────────────────────────────────────────────────────────────────────────────
# MINIMUM RELEASE COUNT FILTERS
# ─────────────────────────────────────────────────────────────────────────────

def filter_by_min_artist_releases(
    df: pd.DataFrame,
    min_releases: int,
) -> pd.DataFrame:
    """
    Keep rows for artists whose total release count in df is at least
    min_releases.

    A release is counted per distinct release_id for that artist, so an
    artist appearing on two labels for the same release counts once.
    Artists below the threshold are removed, along with all their rows.
    """
    if min_releases <= 0 or "artist_id" not in df.columns:
        return df

    if "release_id" in df.columns:
        # Count distinct releases per artist, not rows (avoids inflation
        # from multi-label releases counting multiple times).
        counts = (
            df.drop_duplicates(subset=["artist_id", "release_id"])
            .groupby("artist_id")
            .size()
        )
    else:
        counts = df.groupby("artist_id").size()

    qualifying = counts[counts >= int(min_releases)].index
    return df[df["artist_id"].isin(qualifying)].copy()
