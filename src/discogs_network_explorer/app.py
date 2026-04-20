"""
app.py — Streamlit web interface for Discogs Network Explorer.

Architecture — two-phase processing:

  Phase 1 (fetch):
      Triggered by the "Run Analysis" button.  Makes live Discogs API
      calls to build the raw dataset, then stores it in st.session_state.
      Only runs when the button is clicked; results persist across reruns.

  Phase 2 (filter + display):
      Runs on every Streamlit rerender.  Applies all filter controls to
      the cached raw dataset without making further API calls.  This
      lets users explore the data interactively by adjusting sliders
      and multiselects without waiting for another full crawl.

Sidebar layout:
  Authentication        — token input
  Settings              — HTTP cache toggle
  Seed Configuration    — mode selector, seed label / artist IDs
  Discovery Caps        — year range, per-label / per-artist release caps,
                          min releases per label / artist,
                          optional Release Activity Window
  [Run Analysis]
  Result Filters        — appear after fetch; populated from actual data values
  Graph Options         — visualization type and edge threshold

Main area (tabs):
  Results  — filtered DataFrame + CSV download
  Graph    — network visualization + PNG download
  Report   — HTML/ZIP export
"""

from __future__ import annotations

import datetime
import io
import os

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from discogs_network_explorer.backend import (
    build_label_to_artists,
    clear_http_cache,
    enable_http_cache,
    get_artist_name,
    get_catalog_videos,
    get_label_earliest_year,
    get_label_latest_year,
    get_label_name,
    get_master_artist_list,
    get_master_label_rows,
)
from discogs_network_explorer.filters import (
    apply_artists_only_mode,
    apply_label_only_mode,
    apply_label_plus_artist_mode,
    filter_artists_by_activity_window,
    filter_by_country,
    filter_by_format,
    filter_by_genres,
    filter_by_min_artist_releases,
    filter_by_role,
    filter_by_styles,
    filter_by_year_range,
    filter_labels_by_activity_window,
    filter_labels_by_size,
    unique_labels,
)
from discogs_network_explorer.graph_utils import (
    build_artist_label_graph,
    build_label_label_graph,
    draw_graph_matplotlib,
)
from discogs_network_explorer.report import (
    generate_report_html,
    generate_report_zip,
)
from discogs_network_explorer.youtube import (
    add_video_to_playlist,
    authenticate,
    clear_credentials,
    create_playlist,
    get_stored_token_path,
    get_youtube_service,
    load_credentials,
    search_video,
)
from discogs_network_explorer.apple_music import (
    clear_credentials as am_clear_credentials,
    create_playlist as am_create_playlist,
    add_songs_to_playlist as am_add_songs_to_playlist,
    is_connected as am_is_connected,
    load_config as am_load_config,
    load_user_token as am_load_user_token,
    save_config as am_save_config,
    save_user_token as am_save_user_token,
    search_song as am_search_song,
)

# Current calendar year used as the upper bound for year-range sliders.
CURRENT_YEAR: int = datetime.date.today().year


# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="dne — Discogs Network Explorer", layout="wide")
st.title("dne — Discogs Network Explorer")

# Load .env from the project root or home directory (dev convenience only).
for _env_path in [".", os.path.expanduser("~")]:
    _env_file = os.path.join(_env_path, ".env")
    if os.path.exists(_env_file):
        load_dotenv(_env_file, override=False)
        break


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR — AUTHENTICATION
# ─────────────────────────────────────────────────────────────────────────────

st.sidebar.header("Authentication")

token_input = st.sidebar.text_input(
    "Discogs personal access token",
    value="",
    type="password",
    help="Generate a token at discogs.com → Settings → Developers.",
)

if token_input:
    # Strip whitespace to guard against trailing newlines or spaces from copy-paste.
    os.environ["DISCOGS_TOKEN"] = token_input.strip()

if not os.getenv("DISCOGS_TOKEN"):
    st.warning("Enter your Discogs personal access token in the sidebar to continue.")
    st.stop()


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR — SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

st.sidebar.header("Settings")

cache_on = st.sidebar.checkbox(
    "Enable HTTP cache (24 h)",
    value=True,
    help="Caches successful Discogs API responses for 24 hours.  Recommended: "
         "makes repeated runs instant.  Only HTTP 200 responses are stored — "
         "error responses are never cached.",
)
if cache_on:
    enable_http_cache()

if st.sidebar.button(
    "Clear cache",
    help="Delete all cached API responses.  Use this if you are seeing "
         "unexpected 401 or stale-data errors from a previous session.",
):
    clear_http_cache()
    st.sidebar.success("Cache cleared.")


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR — SEED CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

st.sidebar.header("Seed Configuration")

seed_mode = st.sidebar.selectbox(
    "Seed mode",
    ["Labels Only", "Labels + Artists", "Artists Only"],
    help=(
        "Labels Only — crawl from label(s) and find related labels.\n"
        "Labels + Artists — require all explicit artists AND label overlap.\n"
        "Artists Only — find labels that contain at least X artists from the input list."
    ),
)

st.sidebar.subheader("Seed IDs")

seed_labels_raw = st.sidebar.text_area(
    "Seed label IDs (comma or newline separated)",
    "3661094,1390196",
)
seed_label_ids: list[str] = [
    s.strip()
    for s in seed_labels_raw.replace("\n", ",").split(",")
    if s.strip()
]

seed_artists_raw = st.sidebar.text_area(
    "Seed artist IDs (comma or newline separated)",
    "",
    help="Required for 'Labels + Artists' and 'Artists Only' modes.",
)
seed_artist_ids: list[str] = [
    s.strip()
    for s in seed_artists_raw.replace("\n", ",").split(",")
    if s.strip()
]


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR — DISCOVERY CAPS
# ─────────────────────────────────────────────────────────────────────────────

st.sidebar.header("Discovery Caps")

st.sidebar.caption(
    "Year range and release caps control what the API crawl fetches. "
    "Changes here require clicking **Run Analysis** again."
)

# ── Release year range (fetch-time filter) ───────────────────────────────────
# Controls which releases are considered during the API crawl.
# Releases outside [fetch_year_min, fetch_year_max] are skipped before
# the more expensive release-detail API call is made.

fetch_year_min, fetch_year_max = st.sidebar.slider(
    "Release year range",
    min_value=1900,
    max_value=CURRENT_YEAR,
    value=(2016, CURRENT_YEAR),
    step=1,
    help="Only releases within this year range are fetched.  Narrowing the "
         "window reduces crawl time and dataset size.",
)

# ── Per-entity release caps ───────────────────────────────────────────────────
# Hard limits on how many releases are fetched per seed label / artist.
# These cap API cost; they do not filter by quality.

max_releases_per_label = st.sidebar.number_input(
    "Max releases per discovered label (0 = no limit)",
    min_value=0,
    max_value=50_000,
    value=40,
    step=10,
    help="Exclude any discovered label whose total Discogs catalog exceeds "
         "this number. Labels are screened and discarded as early as possible "
         "during the crawl to avoid wasting API calls on large ambient labels "
         "(e.g. Resident Advisor, Dekmantel). 0 = no limit. Adds one cached "
         "API call per newly-encountered label on first run.",
)

max_releases_per_artist = st.sidebar.slider(
    "Max releases per artist",
    min_value=2,
    max_value=200,
    value=40,
    step=1,
    help="Maximum releases fetched per discovered artist. "
         "Each release requires one extra API call for label data.",
)

# ── Minimum release thresholds (post-fetch quality filters) ──────────────────
# These are applied to the fetched dataset rather than during the crawl.
# Conceptually grouped here because they answer "how active must a
# label / artist be to appear in results?"

min_releases_per_label = st.sidebar.slider(
    "Min releases per label",
    min_value=0,
    max_value=100,
    value=2,
    step=1,
    help="Labels with fewer than this many releases in the fetched dataset "
         "are excluded from results.  0 = no minimum.",
)

min_releases_per_artist = st.sidebar.slider(
    "Min releases per artist",
    min_value=0,
    max_value=50,
    value=1,
    step=1,
    help="Artists with fewer than this many distinct releases in the fetched "
         "dataset are excluded from results.  0 = no minimum.",
)

# ── Release Activity Window (optional) ───────────────────────────────────────
# When enabled, labels — and artists in artist-seed modes — must have at
# least a specified number of releases within a defined year window.
# This confirms ongoing activity and excludes dormant entities.

st.sidebar.markdown("---")
activity_window_on = st.sidebar.checkbox(
    "Release Activity Window",
    value=False,
    help="Require that labels (and artists in artist-seed modes) have a "
         "minimum number of releases within a specified year window.  "
         "Useful for excluding labels or artists that were active only "
         "outside your period of interest.",
)

# Default values used whether or not the section is shown.
activity_window_start: int = 2016
activity_window_end: int   = CURRENT_YEAR
activity_min_releases: int = 1

if activity_window_on:
    activity_window_start, activity_window_end = st.sidebar.slider(
        "Activity window year range",
        min_value=1970,
        max_value=CURRENT_YEAR,
        value=(2016, CURRENT_YEAR),
        step=1,
        help="Labels / artists must have at least the required number of "
             "releases with a release year that falls within this window.",
    )

    activity_min_releases = st.sidebar.slider(
        "Min releases within activity window",
        min_value=1,
        max_value=50,
        value=1,
        step=1,
        help="Number of releases required within the activity window. "
             "Increase to require sustained recent activity.",
    )


# ── Mode A / C overlap controls ───────────────────────────────────────────────
min_overlaps_required: int = 2
strict_per_label: bool = False
artist_min_overlaps: int = 2

if seed_mode == "Labels Only":
    st.sidebar.markdown("---")
    st.sidebar.subheader("Labels Only — overlap tuning")
    min_overlaps_required = st.sidebar.number_input(
        "Min shared artists from seed union",
        min_value=1,
        max_value=50,
        value=2,
        step=1,
        help="A discovered label must share at least this many artists with "
             "the combined pool of all seed labels.",
    )
    strict_per_label = st.sidebar.checkbox(
        "Strict mode — require overlap with every seed label",
        value=False,
        help="When enabled, a discovered label must share at least one artist "
             "with each individual seed label (in addition to the union "
             "overlap threshold above).",
    )

elif seed_mode == "Artists Only":
    st.sidebar.markdown("---")
    st.sidebar.subheader("Artists Only — overlap tuning")
    artist_min_overlaps = st.sidebar.number_input(
        "Min shared artists from input list",
        min_value=1,
        max_value=50,
        value=2,
        step=1,
        help="A discovered label must contain at least this many artists from "
             "the input artist list to appear in results and the graph.",
    )

run_btn = st.sidebar.button("Run Analysis", type="primary", use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — FETCH (runs only on button click)
# ─────────────────────────────────────────────────────────────────────────────

def _validate_inputs() -> None:
    """Stop with an error message if the required seed IDs are missing."""
    if seed_mode in {"Labels Only", "Labels + Artists"} and not seed_label_ids:
        st.error(f"Mode '{seed_mode}' requires at least one seed label ID.")
        st.stop()
    if seed_mode in {"Labels + Artists", "Artists Only"} and not seed_artist_ids:
        st.error(f"Mode '{seed_mode}' requires at least one seed artist ID.")
        st.stop()


if run_btn:
    _validate_inputs()

    # Step 1a: build the artist pool from seed labels (if applicable).
    if seed_mode in {"Labels Only", "Labels + Artists"}:
        with st.spinner("Crawling seed labels for artists…"):
            label_artists = get_master_artist_list(
                seed_label_ids,
                min_year=fetch_year_min,
                max_year=fetch_year_max,
            )
        if seed_mode == "Labels + Artists":
            artists: set[str] = set(label_artists) | {str(a) for a in seed_artist_ids}
        else:
            artists = set(label_artists)
    else:
        artists = {str(a) for a in seed_artist_ids}

    if not artists:
        st.error(
            "No artists discovered from your seeds. "
            "Try increasing Max releases per seed label, widening the year "
            "range, or checking your seed IDs."
        )
        st.stop()

    # Step 1b: expand artists into per-release label rows.
    with st.spinner(
        f"Fetching releases for {len(artists)} artist(s) — "
        "first run may take many minutes depending on number of artists, "
        "subsequent runs use cache…"
    ):
        rows = get_master_label_rows(
            artists,
            max_releases_allowed=max_releases_per_artist,
            min_year=fetch_year_min,
            max_year=fetch_year_max,
            seed_label_ids=seed_label_ids,
            max_global_releases=(
                max_releases_per_label if max_releases_per_label > 0 else None
            ),
        )

    if not rows:
        st.error(
            "No release rows returned from Discogs. "
            "Try widening the year range or adjusting your seeds."
        )
        st.stop()

    _COL_ORDER = [
        "artist_id", "artist_name",
        "label_id", "label_name",
        "release_id", "release_title",
        "role", "format", "genres", "styles", "country", "year",
    ]
    df_raw = pd.DataFrame(rows)
    # Enforce a stable column order so the CSV export is always consistent.
    df_raw = df_raw.reindex(
        columns=[c for c in _COL_ORDER if c in df_raw.columns]
        + [c for c in df_raw.columns if c not in _COL_ORDER]
    )

    # Collect earliest/latest release year for every label in df_raw.
    # Done here (Phase 1) so the data is fetched once and cached in
    # session state — Phase 2 filter reruns never make year API calls.
    all_label_ids = list(df_raw["label_id"].astype(str).unique())
    label_years_map: dict[str, dict[str, int | None]] = {}
    progress = st.progress(0, text="Collecting label year data…")
    for idx, lid in enumerate(all_label_ids):
        label_years_map[lid] = {
            "earliest": get_label_earliest_year(lid),
            "latest":   get_label_latest_year(lid),
        }
        progress.progress(
            (idx + 1) / len(all_label_ids),
            text=f"Collecting label year data… {idx + 1}/{len(all_label_ids)}",
        )
    progress.empty()

    # Persist the raw dataset and crawl parameters so filter controls can
    # operate on it without triggering another API crawl.
    st.session_state["df_raw"]              = df_raw
    st.session_state["artists"]             = artists
    st.session_state["seed_label_ids_used"] = seed_label_ids
    st.session_state["seed_artist_ids_used"]= seed_artist_ids
    st.session_state["seed_mode_used"]      = seed_mode
    st.session_state["label_years"]         = label_years_map

    st.success(
        f"Crawl complete: {len(artists)} artists, "
        f"{len(df_raw):,} release-label rows across "
        f"{df_raw['label_id'].nunique():,} unique labels."
    )


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — FILTER + DISPLAY (runs on every rerender if data exists)
# ─────────────────────────────────────────────────────────────────────────────

if "df_raw" not in st.session_state:
    st.info(
        "Configure your seeds and discovery caps in the sidebar, "
        "then click **Run Analysis**."
    )
    st.stop()

df_raw: pd.DataFrame     = st.session_state["df_raw"]
_artists: set[str]       = st.session_state["artists"]
_seed_labels_used: list[str]  = st.session_state["seed_label_ids_used"]
_seed_artists_used: list[str] = st.session_state["seed_artist_ids_used"]
_seed_mode_used: str          = st.session_state["seed_mode_used"]
_all_label_years: dict[str, dict[str, int | None]] = st.session_state.get(
    "label_years", {}
)


# ── Post-fetch filter sidebar ────────────────────────────────────────────────

st.sidebar.markdown("---")
st.sidebar.header("Result Filters")
st.sidebar.caption("Adjust these without re-fetching.")

# Year range — finer display-level control independent of the fetch window.
_year_min_data = int(df_raw["year"].dropna().min()) if df_raw["year"].notna().any() else 1900
_year_max_data = int(df_raw["year"].dropna().max()) if df_raw["year"].notna().any() else CURRENT_YEAR

_col_y1, _col_y2 = st.sidebar.columns(2)
filter_min_year = _col_y1.number_input(
    "Year from", min_value=1900, max_value=CURRENT_YEAR,
    value=_year_min_data, step=1,
)
filter_max_year = _col_y2.number_input(
    "Year to", min_value=1900, max_value=CURRENT_YEAR,
    value=_year_max_data, step=1,
)

# Label size range.
_size_max_possible = int(df_raw.groupby("label_id").size().max()) if not df_raw.empty else 1000
filter_min_label_size = st.sidebar.number_input(
    "Min label size (rows)", min_value=0, max_value=_size_max_possible, value=0, step=1,
)
filter_max_label_size = st.sidebar.number_input(
    "Max label size (rows)", min_value=0, max_value=_size_max_possible,
    value=_size_max_possible, step=1,
)

# Format, country, role, genre, style — populated from dataset values.
_available_formats = sorted(f for f in df_raw["format"].dropna().unique() if str(f).strip())
selected_formats: list[str] = st.sidebar.multiselect(
    "Format", options=_available_formats, default=[],
    help="Leave empty to include all formats.",
)

_available_countries = sorted(c for c in df_raw["country"].dropna().unique() if str(c).strip())
selected_countries: list[str] = st.sidebar.multiselect(
    "Country", options=_available_countries, default=[],
    help="Leave empty to include all countries.",
)

_available_roles = sorted(r for r in df_raw["role"].dropna().unique() if str(r).strip())
selected_roles: list[str] = st.sidebar.multiselect(
    "Artist role", options=_available_roles, default=[],
    help="Leave empty to include all roles. "
         "Common values: Main, Appearance, TrackAppearance.",
)

_genre_tokens: set[str] = set()
for _cell in df_raw["genres"].dropna():
    _genre_tokens.update(t.strip() for t in str(_cell).split(",") if t.strip())
selected_genres: list[str] = st.sidebar.multiselect(
    "Genre", options=sorted(_genre_tokens), default=[],
    help="Leave empty to include all genres.",
)

_style_tokens: set[str] = set()
for _cell in df_raw["styles"].dropna():
    _style_tokens.update(t.strip() for t in str(_cell).split(",") if t.strip())
selected_styles: list[str] = st.sidebar.multiselect(
    "Style", options=sorted(_style_tokens), default=[],
    help="Leave empty to include all styles.",
)

# Graph options.
st.sidebar.markdown("---")
st.sidebar.header("Graph Options")

network_mode = st.sidebar.selectbox(
    "Visualization type",
    ["Label → Label (shared artists)", "Artist → Label (bipartite)"],
)

min_shared_artists = st.sidebar.slider(
    "Min shared artists (Label→Label edge threshold)",
    min_value=1, max_value=20, value=1, step=1,
    help="Two labels are connected only if they share at least this many "
         "artists.  Increase to reduce clutter in dense graphs.",
)



# ── Apply filters ─────────────────────────────────────────────────────────────

df: pd.DataFrame = df_raw.copy()

# Year range (display-level).
df = filter_by_year_range(df, min_year=filter_min_year, max_year=filter_max_year)

# Label size.
df = filter_labels_by_size(
    df,
    min_size=filter_min_label_size if filter_min_label_size > 0 else None,
    max_size=filter_max_label_size if filter_max_label_size < _size_max_possible else None,
)

# Minimum release counts per label and per artist.
if min_releases_per_label > 0:
    df = filter_labels_by_size(df, min_size=min_releases_per_label, max_size=None)

if min_releases_per_artist > 0:
    df = filter_by_min_artist_releases(df, min_releases=min_releases_per_artist)

# Narrow-down multiselect filters (empty = no restriction).
if selected_formats:
    df = filter_by_format(df, set(selected_formats))
if selected_countries:
    df = filter_by_country(df, set(selected_countries))
if selected_roles:
    df = filter_by_role(df, set(selected_roles))
if selected_genres:
    df = filter_by_genres(df, set(selected_genres))
if selected_styles:
    df = filter_by_styles(df, set(selected_styles))

# Mode-specific overlap filter — run BEFORE activity window so that
# relevant labels aren't prematurely dropped.  The activity window
# then checks surviving labels via the API (one cached call each).
if _seed_mode_used == "Labels Only":
    df = apply_label_only_mode(
        df,
        seed_label_ids=_seed_labels_used,
        min_overlaps_required=min_overlaps_required,
        strict_per_label=strict_per_label,
        seed_artist_pool=_artists,
    )
elif _seed_mode_used == "Labels + Artists":
    df = apply_label_plus_artist_mode(
        df,
        seed_label_ids=_seed_labels_used,
        seed_artist_ids=_seed_artists_used,
    )
elif _seed_mode_used == "Artists Only":
    df = apply_artists_only_mode(
        df,
        seed_artist_ids=_seed_artists_used,
        min_overlaps_required=artist_min_overlaps,
    )

# Release Activity Window — applied after the overlap filter.
# Uses year data collected during Phase 1 (stored in session state) so
# no API calls are made here.
if activity_window_on and not df.empty:
    seed_ids_set = {str(s) for s in (_seed_labels_used or [])}
    surviving_labels = set(df["label_id"].astype(str).unique())
    inactive_labels: set[str] = set()
    for lid in surviving_labels:
        if lid in seed_ids_set:
            continue  # seed labels always kept
        latest = _all_label_years.get(lid, {}).get("latest")
        if latest is not None and latest < activity_window_start:
            inactive_labels.add(lid)
    if inactive_labels:
        df = df[~df["label_id"].astype(str).isin(inactive_labels)].copy()

    # Apply the same window to artists when seeds include explicit artist IDs.
    if _seed_mode_used in {"Artists Only", "Labels + Artists"}:
        df = filter_artists_by_activity_window(
            df,
            window_start=activity_window_start,
            window_end=activity_window_end,
            min_releases_in_window=activity_min_releases,
        )

# Look up year data from Phase 1 cache for labels surviving filters.
_label_years: dict[str, dict[str, int | None]] = {
    _lid: _all_label_years.get(_lid, {"earliest": None, "latest": None})
    for _lid in df["label_id"].astype(str).unique()
}

if df.empty:
    st.warning(
        "All rows were filtered out by the current settings. "
        "Relax the filters in the sidebar or click Run Analysis with a "
        "wider year range."
    )
    st.stop()

st.caption(
    f"Showing {len(df):,} rows · "
    f"{df['label_id'].nunique():,} unique labels · "
    f"{df['artist_id'].nunique():,} unique artists "
    f"(from {len(df_raw):,} raw rows)"
)


# ─────────────────────────────────────────────────────────────────────────────
# EXCEL OUTPUT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_excel_output(
    df_results: pd.DataFrame,
    df_raw: pd.DataFrame,
    artists: set[str],
    seed_label_ids_used: list[str],
    seed_artist_ids_used: list[str],
    seed_mode_used: str,
    label_names: dict[str, str],
    params: dict,
    label_years: dict[str, dict[str, int | None]] | None = None,
) -> bytes:
    """
    Build a 3-sheet Excel workbook and return it as bytes.

    Sheet 1 — Seed & Run Info
        Seed label IDs/names, search parameters, discovered artist IDs/names.

    Sheet 2 — Release Summary
        Every filtered release row (same content as the old CSV export).

    Sheet 3 — Discovered Labels
        One row per label found during the crawl, with columns:
        label_name, label_id, artists (comma-separated names of seed-pool
        artists on that label), overlap_pct (% of seed-pool artists present).
    """
    # Build artist_id → canonical artist name via the API.
    # Release-credit names vary per release (aliases, collaborations, etc.),
    # so we always use the canonical name from /artists/{id}.  With HTTP
    # caching enabled, each lookup is cached after the first call.
    artist_name_map: dict[str, str] = {}
    for aid in list(artists):
        artist_name_map[aid] = get_artist_name(aid)

    # ── Sheet 1 data ──────────────────────────────────────────────────────────
    seed_label_rows = [
        {"label_id": lid, "label_name": label_names.get(lid, lid)}
        for lid in seed_label_ids_used
    ]
    seed_artist_rows = [
        {"artist_id": aid, "artist_name": artist_name_map.get(aid, f"artist_{aid}")}
        for aid in seed_artist_ids_used
    ] if seed_artist_ids_used else []

    params_rows = [{"parameter": k, "value": str(v)} for k, v in params.items()]

    discovered_artist_rows = [
        {
            "artist_id":   aid,
            "artist_name": artist_name_map.get(aid, f"artist_{aid}"),
        }
        for aid in sorted(artists)
    ]

    # ── Sheet 3 data ──────────────────────────────────────────────────────────
    # Build label → {artist_id: artist_name} from df_raw, keeping only
    # artists that are in the seed pool (_artists).
    label_id_to_name: dict[str, str] = {}
    l2a_names: dict[str, dict[str, str]] = {}  # label_id → {aid: aname}
    for rec in df_raw.to_dict("records"):
        lid   = str(rec.get("label_id", "")).strip()
        lname = str(rec.get("label_name", "")).strip()
        aid   = str(rec.get("artist_id", "")).strip()
        aname = str(rec.get("artist_name", "")).strip()
        if not lid:
            continue
        label_id_to_name.setdefault(lid, lname)
        if aid in artists:
            l2a_names.setdefault(lid, {})[aid] = artist_name_map.get(aid, aname or f"artist_{aid}")

    seed_count = len(artists) if artists else 1
    label_summary_rows = []
    for lid, aid_map in sorted(l2a_names.items(), key=lambda x: len(x[1]), reverse=True):
        # overlap_pct = (unique seed artists on this label) /
        #               (total discovered artists) × 100.
        # The ideal denominator would be the label's full artist roster,
        # but resolving it requires ~30 extra API calls per label (~45 min
        # for 83 labels), so we use the seed-pool size as the denominator.
        overlap_pct = round(len(aid_map) / seed_count * 100, 2)
        # Use set() to deduplicate names — different artist IDs can share
        # a name (e.g. two "Various" placeholders), causing repeats otherwise.
        artist_list = ", ".join(sorted(set(aid_map.values())))
        yrs = (label_years or {}).get(lid, {})
        label_summary_rows.append({
            "label_name":    label_id_to_name.get(lid, lid),
            "label_id":      lid,
            "artists":       artist_list,
            "overlap_pct":   overlap_pct,
            "earliest_year": yrs.get("earliest", ""),
            "latest_year":   yrs.get("latest", ""),
        })

    # ── Write workbook ────────────────────────────────────────────────────────
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        sname1 = "Seed & Run Info"

        # Helper: write a bold-ish section header by putting text in col A,
        # then write a DataFrame starting on the next row.
        def _write_section(title: str, df_section: pd.DataFrame, start_row: int) -> int:
            pd.DataFrame([[title]]).to_excel(
                writer, sheet_name=sname1, startrow=start_row,
                index=False, header=False,
            )
            df_section.to_excel(
                writer, sheet_name=sname1, startrow=start_row + 1, index=False,
            )
            return start_row + 1 + len(df_section) + 2  # +1 header +2 blank gap

        row = 0
        row = _write_section("Seed Labels", pd.DataFrame(seed_label_rows), row)
        if seed_artist_rows:
            row = _write_section("Seed Artists", pd.DataFrame(seed_artist_rows), row)
        row = _write_section("Search Parameters", pd.DataFrame(params_rows), row)
        _artist_section_title = (
            "Input Artists"
            if seed_mode_used == "Artists Only"
            else "Discovered Artists"
        )
        _write_section(
            f"{_artist_section_title} ({len(discovered_artist_rows)})",
            pd.DataFrame(discovered_artist_rows),
            row,
        )

        # Sheet 2 — All Releases: all crawled rows with placeholder artist
        # names resolved to true names using the same artist_name_map built above.
        df_raw_out = df_raw.copy()
        if "artist_name" in df_raw_out.columns and "artist_id" in df_raw_out.columns:
            df_raw_out["artist_name"] = df_raw_out.apply(
                lambda r: artist_name_map.get(str(r["artist_id"]).strip(), r["artist_name"])
                if str(r.get("artist_name", "")).startswith("artist_")
                else r["artist_name"],
                axis=1,
            )
        df_raw_out.to_excel(writer, sheet_name="All Releases", index=False)

        # Sheet 3 — Discovered Labels
        pd.DataFrame(label_summary_rows).to_excel(
            writer, sheet_name="Discovered Labels", index=False,
        )

    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN AREA — TABS
# ─────────────────────────────────────────────────────────────────────────────

# Normalize a raw label_id value to a plain string key, stripping any
# trailing ".0" that pandas introduces when a column contains NaNs and
# is upcast to float64.
def _lid_str(val) -> str:
    s = str(val).strip()
    return s[:-2] if s.endswith(".0") else s

# Build label_names from df_raw so seed labels filtered out of df still
# have human-readable names when forced into the graph.  Defined here
# (before the tabs) so it is available in tab_results (download button
# data is computed on every render, not deferred to click time) as well
# as in tab_graph and tab_report.
_label_names = {
    _lid_str(r["label_id"]): str(r["label_name"]).strip()
    for r in df_raw.to_dict("records")
    if r.get("label_id") and r.get("label_name")
}
_seed_ids = [str(s) for s in _seed_labels_used]

# Ensure every seed label has a human-readable name.  Seed labels that
# have no rows in df_raw (all releases outside the year range) won't
# appear in _label_names, so we fall back to a direct API call.
for _sid in _seed_ids:
    if _sid not in _label_names:
        _label_names[_sid] = get_label_name(_sid)

# Build label→artists mapping from df_raw for seed-label backfill.
_l2a_raw = build_label_to_artists(df_raw.to_dict("records"))

tab_results, tab_graph, tab_report = st.tabs(["Results", "Graph", "Report"])


# ── Tab: Results ─────────────────────────────────────────────────────────────

with tab_results:
    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.subheader("Release rows (first 200)")
        st.dataframe(df.head(200), use_container_width=True)

        _excel_params = {
            "seed_mode":               _seed_mode_used,
            "fetch_year_min":          fetch_year_min,
            "fetch_year_max":          fetch_year_max,
            "max_releases_per_artist": max_releases_per_artist,
            "max_releases_per_label":  max_releases_per_label or "no limit",
            "min_releases_per_label":  min_releases_per_label or "no minimum",
            "min_releases_per_artist": min_releases_per_artist or "no minimum",
            "activity_window": (
                f"{activity_window_start}–{activity_window_end} "
                f"(min {activity_min_releases})"
                if activity_window_on else "off"
            ),
            "filter_year_range":  f"{filter_min_year}–{filter_max_year}",
            "filter_formats":     ", ".join(selected_formats) or "all",
            "filter_countries":   ", ".join(selected_countries) or "all",
            "filter_roles":       ", ".join(selected_roles) or "all",
            "filter_genres":      ", ".join(selected_genres) or "all",
            "filter_styles":      ", ".join(selected_styles) or "all",
        }
        _excel_bytes = _build_excel_output(
            df_results=df,
            df_raw=df_raw,
            artists=_artists,
            seed_label_ids_used=_seed_labels_used,
            seed_artist_ids_used=_seed_artists_used,
            seed_mode_used=_seed_mode_used,
            label_names=_label_names,
            params=_excel_params,
            label_years=_label_years,
        )
        st.download_button(
            "Download results as Excel (.xlsx)",
            data=_excel_bytes,
            file_name="discogs_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    with col_right:
        st.subheader("Unique labels (top 100)")
        st.dataframe(unique_labels(df).head(100), use_container_width=True)

    with st.expander("Debug info"):
        st.json(
            {
                "seed_mode":       _seed_mode_used,
                "seed_labels":     _seed_labels_used,
                "seed_artists":    _seed_artists_used,
                "artists_crawled": len(_artists),
                "fetch_year_min":  fetch_year_min,
                "fetch_year_max":  fetch_year_max,
                "raw_rows":        len(df_raw),
                "filtered_rows":   len(df),
                "unique_labels":   int(df["label_id"].nunique()),
                "unique_artists":  int(df["artist_id"].nunique()),
                "activity_window": (
                    f"{activity_window_start}–{activity_window_end} "
                    f"(min {activity_min_releases} release(s))"
                    if activity_window_on else "off"
                ),
            }
        )


# ── Tab: Graph ───────────────────────────────────────────────────────────────

with tab_graph:
    fig, ax = plt.subplots(figsize=(10, 7))

    if network_mode.startswith("Label"):
        l2a = build_label_to_artists(df.to_dict("records"))
        # Seed labels may be filtered out of df by post-fetch controls; restore
        # their artist sets from df_raw so they retain edges in the graph.
        for _sid in _seed_ids:
            if not l2a.get(_sid):
                l2a[_sid] = _l2a_raw.get(_sid, set())
        G = build_label_label_graph(
            l2a,
            min_shared=min_shared_artists,
            label_names=_label_names,
            seed_label_ids=_seed_ids,
            seed_artist_union=_artists,  # authoritative pool from Phase 1 crawl
            label_years=_label_years,
        )
    else:
        G = build_artist_label_graph(
            df.to_dict("records"),
            seed_label_ids=_seed_ids,
            label_names=_label_names,
        )

    if G.number_of_nodes() == 0:
        st.warning(
            "The graph has no nodes after filtering. "
            "Try lowering the 'Min shared artists' threshold or relaxing "
            "other filters."
        )
    else:
        draw_graph_matplotlib(G, ax=ax)

        # Save to buffer BEFORE st.pyplot() — clear_figure=True wipes the
        # figure immediately after display, producing a blank file if saved after.
        _graph_buf = __import__("io").BytesIO()
        fig.savefig(_graph_buf, format="png", bbox_inches="tight", dpi=150)
        _graph_buf.seek(0)
        st.session_state["graph_fig"] = fig

        st.pyplot(fig, clear_figure=True)

        st.download_button(
            "Download graph as PNG",
            data=_graph_buf.getvalue(),
            file_name="discogs_graph.png",
            mime="image/png",
        )

    st.caption(
        f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges.  \n"
        f"Node fill color = latest release year · Border color = earliest release year"
    )


# ── Tab: Report ──────────────────────────────────────────────────────────────

with tab_report:
    st.subheader("Export analysis report")
    st.write(
        "Generate a self-contained HTML report with embedded graph and "
        "metadata, or download a ZIP bundle containing the HTML, the full "
        "CSV, and the graph PNG."
    )

    if st.button("Generate report"):
        _report_fig = st.session_state.get("graph_fig")

        if _report_fig is None:
            # Build a figure if the Graph tab has not been rendered yet.
            _report_fig, _ax = plt.subplots(figsize=(10, 7))
            _rep_label_names = {
                _lid_str(r["label_id"]): str(r["label_name"]).strip()
                for r in df_raw.to_dict("records")
                if r.get("label_id") and r.get("label_name")
            }
            _rep_seed_ids = [str(s) for s in _seed_labels_used]

            if network_mode.startswith("Label"):
                _l2a = build_label_to_artists(df.to_dict("records"))
                for _sid in _rep_seed_ids:
                    if not _l2a.get(_sid):
                        _l2a[_sid] = _l2a_raw.get(_sid, set())
                _G = build_label_label_graph(
                    _l2a,
                    min_shared=min_shared_artists,
                    label_names=_rep_label_names,
                    seed_label_ids=_rep_seed_ids,
                    seed_artist_union=_artists,
                    label_years=_label_years,
                )
            else:
                _G = build_artist_label_graph(
                    df.to_dict("records"),
                    seed_label_ids=_rep_seed_ids,
                    label_names=_rep_label_names,
                )
            if _G.number_of_nodes() > 0:
                draw_graph_matplotlib(_G, ax=_ax)

        metadata = {
            "seed_mode":              _seed_mode_used,
            "seed_label_ids":         _seed_labels_used,
            "seed_artist_ids":        _seed_artists_used,
            "fetch_year_range":       f"{fetch_year_min}–{fetch_year_max}",
            "max_releases_per_label": max_releases_per_label,
            "max_releases_per_artist":max_releases_per_artist,
            "min_releases_per_label": min_releases_per_label,
            "min_releases_per_artist":min_releases_per_artist,
            "activity_window":        (
                f"{activity_window_start}–{activity_window_end} "
                f"(min {activity_min_releases})"
                if activity_window_on else "off"
            ),
            "filter_year_range":      f"{filter_min_year}–{filter_max_year}",
            "filter_formats":         selected_formats or "all",
            "filter_countries":       selected_countries or "all",
            "filter_roles":           selected_roles or "all",
            "filter_genres":          selected_genres or "all",
            "filter_styles":          selected_styles or "all",
            "result_rows":            len(df),
            "unique_labels":          int(df["label_id"].nunique()),
        }

        html_report = generate_report_html(
            seed_label_ids=_seed_labels_used,
            seed_artist_ids=_seed_artists_used,
            master_artist_pools=list(_artists)[:200],
            df_results=df,
            graph_fig=_report_fig,
            metadata=metadata,
        )

        zip_bytes = generate_report_zip(
            html_report=html_report,
            df_results=df,
            graph_fig=_report_fig,
        )

        st.download_button(
            "Download HTML report",
            data=html_report.encode("utf-8"),
            file_name="discogs_report.html",
            mime="text/html",
        )
        st.download_button(
            "Download ZIP bundle (HTML + CSV + PNG)",
            data=zip_bytes,
            file_name="discogs_report.zip",
            mime="application/zip",
        )


# ─────────────────────────────────────────────────────────────────────────────
# DNX — DISCOGS NETWORK XTRACTOR (playlist builder)
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("---")
st.header("dnx — Discogs Network Xtractor")
st.caption(
    "Build a playlist from Discogs labels or artists discovered above. "
    "Uses community-curated YouTube links from Discogs release pages when "
    "available, falling back to search for releases without links."
)

# ── Platform selector ───────────────────────────────────────────────────────

dnx_platform = st.radio(
    "Platform",
    ["YouTube", "Apple Music"],
    horizontal=True,
    key="dnx_platform",
)

# ── YouTube authentication ──────────────────────────────────────────────────

if dnx_platform == "YouTube":
    _yt_creds = load_credentials()
    _yt_connected = _yt_creds is not None

    col_yt_status, col_yt_action = st.columns([3, 1])
    with col_yt_status:
        if _yt_connected:
            st.success("YouTube connected.")
        else:
            st.info("YouTube not connected. Provide your OAuth client secret JSON to connect.")

    with col_yt_action:
        if _yt_connected:
            if st.button("Disconnect YouTube"):
                clear_credentials()
                st.rerun()

    if not _yt_connected:
        _client_secret_path = st.text_input(
            "Path to Google OAuth client_secret JSON",
            value=os.path.expanduser("~/client_secret.json"),
            help="Download this from Google Cloud Console → APIs & Services → Credentials.",
        )
        if st.button("Connect YouTube"):
            if not os.path.isfile(_client_secret_path):
                st.error(f"File not found: {_client_secret_path}")
            else:
                try:
                    with st.spinner("Opening browser for Google authorization…"):
                        _yt_creds = authenticate(_client_secret_path)
                    _yt_connected = True
                    st.success("YouTube connected! Token saved to " + str(get_stored_token_path()))
                    st.rerun()
                except Exception as exc:
                    st.error(f"YouTube authorization failed: {exc}")

    if not _yt_connected:
        st.stop()

# ── Apple Music authentication ──────────────────────────────────────────────

if dnx_platform == "Apple Music":
    _am_config = am_load_config()
    _am_user_token = am_load_user_token()
    _am_connected = _am_config is not None and _am_user_token is not None

    col_am_status, col_am_action = st.columns([3, 1])
    with col_am_status:
        if _am_connected:
            st.success("Apple Music connected.")
        else:
            st.info(
                "Apple Music not connected. Provide your MusicKit credentials below. "
                "See apple_music_setup.txt for setup instructions."
            )

    with col_am_action:
        if _am_connected:
            if st.button("Disconnect Apple Music"):
                am_clear_credentials()
                st.rerun()

    if not _am_connected:
        with st.expander("Apple Music developer credentials", expanded=True):
            am_team_id = st.text_input(
                "Team ID",
                help="10-character alphanumeric ID from Apple Developer account.",
            )
            am_key_id = st.text_input(
                "Key ID",
                help="10-character alphanumeric MusicKit key ID.",
            )
            am_p8_path = st.text_input(
                "Path to .p8 private key file",
                value=os.path.expanduser("~/AuthKey.p8"),
                help="Downloaded from Apple Developer portal → Certificates, Identifiers & Profiles → Keys.",
            )
            am_user_token_input = st.text_input(
                "Music User Token",
                type="password",
                help=(
                    "Obtained via MusicKit JS authorization in a browser. "
                    "See apple_music_setup.txt for how to get this token."
                ),
            )
            if st.button("Connect Apple Music"):
                if not am_team_id or not am_key_id:
                    st.error("Team ID and Key ID are required.")
                elif not os.path.isfile(am_p8_path):
                    st.error(f"Private key file not found: {am_p8_path}")
                elif not am_user_token_input.strip():
                    st.error("Music User Token is required.")
                else:
                    try:
                        dev_token = am_save_config(am_team_id, am_key_id, am_p8_path)
                        am_save_user_token(am_user_token_input)
                        _am_connected = True
                        _am_config = am_load_config()
                        _am_user_token = am_user_token_input.strip()
                        st.success("Apple Music connected! Credentials saved to ~/.dne/")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Apple Music setup failed: {exc}")

    if not _am_connected:
        st.stop()

    am_storefront = st.text_input(
        "Apple Music storefront",
        value="us",
        help="ISO 3166-1 alpha-2 country code for your Apple Music subscription (e.g. us, gb, de).",
    )

# ── dnx input ───────────────────────────────────────────────────────────────

# Build a lookup from label names → label IDs using the current dataset.
_name_to_lid: dict[str, str] = {}
for _lid_key, _lname in _label_names.items():
    _name_to_lid[_lname.lower()] = _lid_key

dnx_input_type = st.radio(
    "Input type",
    ["Label names", "Label IDs", "Artist IDs"],
    horizontal=True,
)

if dnx_input_type == "Label names":
    dnx_raw = st.text_input(
        "Label names (comma separated)",
        placeholder="e.g. Livity Sound, NAFF",
        help="Names are matched against labels in the current dataset (case-insensitive).",
    )
elif dnx_input_type == "Label IDs":
    dnx_raw = st.text_input(
        "Label IDs (comma separated)",
        placeholder="e.g. 251117, 1390196",
    )
else:
    dnx_raw = st.text_input(
        "Artist IDs (comma separated)",
        placeholder="e.g. 1606986, 2742670",
    )

dnx_max_releases = st.slider(
    "Max releases per label/artist",
    min_value=1,
    max_value=200,
    value=30,
    help=(
        "Limits how many releases to fetch per input from the full Discogs catalog. "
        "Each release may contribute multiple videos (one per track)."
    ),
)

dnx_col1, dnx_col2 = st.columns(2)
with dnx_col1:
    dnx_min_year = st.number_input(
        "dnx min year",
        min_value=1900,
        max_value=CURRENT_YEAR,
        value=1900,
        help="Only include releases from this year onward.",
    )
with dnx_col2:
    dnx_max_year = st.number_input(
        "dnx max year",
        min_value=1900,
        max_value=CURRENT_YEAR,
        value=CURRENT_YEAR,
        help="Only include releases up to this year.",
    )

dnx_search_fallback = st.checkbox(
    f"{'YouTube' if dnx_platform == 'YouTube' else 'Apple Music'} search fallback for releases without Discogs video links",
    value=False if dnx_platform == "YouTube" else True,
    help=(
        "When enabled, releases without embedded YouTube links on their "
        "Discogs page will be searched by artist + title. "
        + ("Costs 100 YouTube API quota units per search (daily limit: 10,000)."
           if dnx_platform == "YouTube"
           else "Apple Music catalog search has no quota limit.")
    ),
)

dnx_playlist_name = st.text_input(
    "Playlist name",
    value=f"dnx — {datetime.date.today().isoformat()}",
)

# ── Resolve inputs and fetch full catalogs ─────────────────────────────────


def _extract_yt_video_id(url: str) -> str | None:
    """Extract YouTube video ID from a watch or youtu.be URL."""
    import re
    m = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
    return m.group(1) if m else None


if st.button(f"Build {'YouTube' if dnx_platform == 'YouTube' else 'Apple Music'} Playlist", type="primary"):
    entries = [e.strip() for e in dnx_raw.split(",") if e.strip()]
    if not entries:
        st.error("Enter at least one label or artist.")
        st.stop()

    # Resolve label names to IDs if needed.
    if dnx_input_type == "Label names":
        resolved_lids: list[str] = []
        unresolved: list[str] = []
        for name in entries:
            lid = _name_to_lid.get(name.lower())
            if lid:
                resolved_lids.append(lid)
            else:
                unresolved.append(name)
        if unresolved:
            st.warning(f"Could not match label names: {', '.join(unresolved)}")
        if not resolved_lids:
            st.error("No labels matched. Check spelling or use label IDs instead.")
            st.stop()
        entity_type = "label"
        entity_ids = resolved_lids
    elif dnx_input_type == "Label IDs":
        entity_type = "label"
        entity_ids = entries
    else:
        entity_type = "artist"
        entity_ids = entries

    # Year filter — use None when set to boundary values (no filtering).
    eff_min_year = dnx_min_year if dnx_min_year > 1900 else None
    eff_max_year = dnx_max_year if dnx_max_year < CURRENT_YEAR else None

    # ── Fetch full catalogs via Discogs API ────────────────────────────────

    all_releases: list[dict] = []
    progress = st.progress(0, text="Fetching catalogs from Discogs…")
    for eidx, eid in enumerate(entity_ids):
        try:
            catalog = get_catalog_videos(
                entity_type=entity_type,
                entity_id=eid,
                max_releases=dnx_max_releases,
                min_year=eff_min_year,
                max_year=eff_max_year,
            )
            all_releases.extend(catalog)
        except Exception as exc:
            st.warning(f"Failed to fetch catalog for {entity_type} {eid}: {exc}")
        progress.progress(
            (eidx + 1) / len(entity_ids),
            text=f"Fetching catalogs… {eidx + 1}/{len(entity_ids)}",
        )
    progress.empty()

    if not all_releases:
        st.warning("No releases found. Check the IDs and year range.")
        st.stop()

    # ── Collect videos/tracks from fetched releases ─────────────────────────

    video_queue: list[dict] = []
    search_queue: list[dict] = []
    no_embed_count = 0

    if dnx_platform == "YouTube":
        # YouTube path — prefer embedded Discogs video links.
        for rel in all_releases:
            vids = rel.get("videos") or []
            if vids:
                for v in vids:
                    vid_id = _extract_yt_video_id(v["url"])
                    if vid_id:
                        video_queue.append({
                            "video_id": vid_id,
                            "title":    v["title"],
                            "artist":   rel["artist_name"],
                            "release":  rel["release_title"],
                            "label":    rel.get("label_name", ""),
                            "source":   "discogs",
                        })
            else:
                tracklist = rel.get("tracklist") or []
                if tracklist:
                    for track in tracklist:
                        if track["artist"] and track["title"]:
                            search_queue.append({
                                "artist":  track["artist"],
                                "title":   track["title"],
                                "release": rel["release_title"],
                                "label":   rel.get("label_name", ""),
                                "rid":     rel["release_id"],
                            })
                no_embed_count += 1

        releases_with_vids = len(all_releases) - no_embed_count
        st.info(
            f"Fetched **{len(all_releases)}** releases. "
            f"Found **{len(video_queue)}** embedded videos across "
            f"**{releases_with_vids}** releases. "
            f"**{no_embed_count}** releases have no Discogs video links"
            + (f" ({len(search_queue)} tracks queued for search)." if search_queue else ".")
        )

    else:
        # Apple Music path — all tracks go to search (no embeds on Discogs).
        for rel in all_releases:
            tracklist = rel.get("tracklist") or []
            if tracklist:
                for track in tracklist:
                    if track["artist"] and track["title"]:
                        search_queue.append({
                            "artist":  track["artist"],
                            "title":   track["title"],
                            "release": rel["release_title"],
                            "label":   rel.get("label_name", ""),
                            "rid":     rel["release_id"],
                        })
            else:
                # No tracklist — use release-level info.
                if rel["artist_name"] and rel["release_title"]:
                    search_queue.append({
                        "artist":  rel["artist_name"],
                        "title":   rel["release_title"],
                        "release": rel["release_title"],
                        "label":   rel.get("label_name", ""),
                        "rid":     rel["release_id"],
                    })

        st.info(
            f"Fetched **{len(all_releases)}** releases. "
            f"**{len(search_queue)}** tracks queued for Apple Music search."
        )

    # ── Search fallback (per-track) ───────────────────────────────────────

    search_errors: list[str] = []

    if dnx_search_fallback and search_queue:
        if dnx_platform == "YouTube":
            yt_service = get_youtube_service(_yt_creds)
            progress = st.progress(0, text="Searching YouTube for individual tracks…")
            for idx, item in enumerate(search_queue):
                query = f"{item['artist']} - {item['title']}"
                try:
                    result = search_video(yt_service, query)
                except Exception as exc:
                    search_errors.append(f"{query}: {exc}")
                    result = None
                if result:
                    video_queue.append({
                        "video_id": result["video_id"],
                        "title":    result["title"],
                        "artist":   item["artist"],
                        "release":  item["release"],
                        "label":    item["label"],
                        "source":   "yt_search",
                    })
                progress.progress(
                    (idx + 1) / len(search_queue),
                    text=f"YouTube search… {idx + 1}/{len(search_queue)} tracks",
                )
            progress.empty()

        else:
            # Apple Music search.
            _am_dev_token = _am_config["developer_token"]
            progress = st.progress(0, text="Searching Apple Music for tracks…")
            for idx, item in enumerate(search_queue):
                query = f"{item['artist']} - {item['title']}"
                try:
                    result = am_search_song(_am_dev_token, query, storefront=am_storefront)
                except Exception as exc:
                    search_errors.append(f"{query}: {exc}")
                    result = None
                if result:
                    video_queue.append({
                        "video_id": result["song_id"],
                        "title":    result["title"],
                        "artist":   result["artist"],
                        "release":  item["release"],
                        "label":    item["label"],
                        "source":   "am_search",
                    })
                progress.progress(
                    (idx + 1) / len(search_queue),
                    text=f"Apple Music search… {idx + 1}/{len(search_queue)} tracks",
                )
            progress.empty()

        if search_errors:
            with st.expander(f"Search errors ({len(search_errors)})"):
                for err in search_errors:
                    st.text(err)

    if not video_queue:
        if not dnx_search_fallback and search_queue:
            _platform_label = "YouTube" if dnx_platform == "YouTube" else "Apple Music"
            st.warning(
                f"No tracks found. "
                f"Enable **search fallback** above to search {_platform_label} "
                f"for the {len(search_queue)} track(s) by artist + title."
            )
        else:
            st.warning("No tracks found to add.")
        st.stop()

    # ── Deduplicate by video_id / song_id ─────────────────────────────────

    seen_ids: set[str] = set()
    unique_queue: list[dict] = []
    for v in video_queue:
        if v["video_id"] not in seen_ids:
            seen_ids.add(v["video_id"])
            unique_queue.append(v)
    video_queue = unique_queue

    # ── Build playlist ──────────────────────────────────────────────────────

    _playlist_description = (
        f"Auto-generated by dnx (Discogs Network Xtractor) on "
        f"{datetime.date.today().isoformat()}. "
        f"{len(video_queue)} tracks from {len(all_releases)} releases."
    )

    if dnx_platform == "YouTube":
        yt_service = get_youtube_service(_yt_creds)

        playlist_id = create_playlist(
            yt_service,
            title=dnx_playlist_name,
            description=_playlist_description,
        )
        playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
        st.info(f"Created playlist: [{dnx_playlist_name}]({playlist_url})")

        added = 0
        failed = 0
        progress = st.progress(0, text="Adding videos to playlist…")
        results_log: list[dict] = []

        for idx, v in enumerate(video_queue):
            try:
                add_video_to_playlist(yt_service, playlist_id, v["video_id"])
                added += 1
                v["status"] = "added"
            except Exception:
                failed += 1
                v["status"] = "failed"
            results_log.append(v)

            progress.progress(
                (idx + 1) / len(video_queue),
                text=f"Adding to playlist… {idx + 1}/{len(video_queue)} ({added} added)",
            )

        progress.empty()

        discogs_count = sum(1 for r in results_log if r["source"] == "discogs" and r["status"] == "added")
        search_count = sum(1 for r in results_log if r.get("source") in ("yt_search",) and r["status"] == "added")

        st.success(
            f"Done — **{added}** videos added "
            f"({discogs_count} from Discogs, {search_count} from YouTube search), "
            f"**{failed}** failed. "
            f"[Open playlist]({playlist_url})"
        )

    else:
        # Apple Music — batch add songs to playlist.
        _am_dev_token = _am_config["developer_token"]

        playlist_id = am_create_playlist(
            _am_dev_token,
            _am_user_token,
            title=dnx_playlist_name,
            description=_playlist_description,
        )
        st.info(f"Created Apple Music playlist: **{dnx_playlist_name}**")

        # Apple Music supports batch adding — send in chunks of 25.
        added = 0
        failed = 0
        results_log = []
        chunk_size = 25
        progress = st.progress(0, text="Adding songs to playlist…")

        for chunk_start in range(0, len(video_queue), chunk_size):
            chunk = video_queue[chunk_start:chunk_start + chunk_size]
            song_ids = [v["video_id"] for v in chunk]
            try:
                am_add_songs_to_playlist(
                    _am_dev_token, _am_user_token, playlist_id, song_ids,
                )
                for v in chunk:
                    v["status"] = "added"
                    added += 1
            except Exception:
                for v in chunk:
                    v["status"] = "failed"
                    failed += 1
            results_log.extend(chunk)

            progress.progress(
                min((chunk_start + chunk_size), len(video_queue)) / len(video_queue),
                text=f"Adding to playlist… {min(chunk_start + chunk_size, len(video_queue))}/{len(video_queue)} ({added} added)",
            )

        progress.empty()

        search_count = sum(1 for r in results_log if r.get("source") == "am_search" and r["status"] == "added")

        st.success(
            f"Done — **{added}** songs added "
            f"({search_count} from Apple Music search), "
            f"**{failed}** failed."
        )

    with st.expander("Extraction log"):
        log_df = pd.DataFrame(results_log)
        display_cols = [c for c in ["artist", "release", "title", "source", "status", "video_id", "label"] if c in log_df.columns]
        st.dataframe(log_df[display_cols], use_container_width=True)
