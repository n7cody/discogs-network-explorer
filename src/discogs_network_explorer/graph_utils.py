"""
graph_utils.py — Network graph construction and rendering.

Two graph types are supported:

  Label → Label (shared artists)
      Undirected graph where nodes are labels and edges connect pairs of
      labels that share at least min_shared artists.  Edge weight equals
      the number of shared artists.

  Artist → Label (bipartite)
      Bipartite undirected graph with artist nodes and label nodes connected
      by release participation edges.

Node display labels strip the internal "label:" / "artist:" prefix used
for node uniqueness, showing only the human-readable ID or name.
"""

from __future__ import annotations

import networkx as nx
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# Year-based color scale: maps year ranges to hex colors.
# Order matters — checked top-to-bottom, first match wins.
_YEAR_COLOR_SCALE: list[tuple[int, int, str]] = [
    (2026, 2026, "#b150b0"),
    (2025, 2025, "#9750b1"),
    (2023, 2024, "#7750b1"),
    (2022, 2022, "#5950b1"),
    (2021, 2021, "#5064b1"),
    (2020, 2020, "#5089b1"),
    (2016, 2019, "#50aeb1"),
    (2011, 2015, "#50b177"),
    (2006, 2010, "#8bb150"),
    (2000, 2005, "#a4b150"),
    (1995, 1999, "#b1a050"),
    (1990, 1994, "#b18e50"),
    (1980, 1989, "#b17250"),
    (   0, 1979, "#b15750"),
]

_DEFAULT_YEAR_COLOR = "#888888"  # fallback when year is unknown


def _year_to_color(year: int | None) -> str:
    """Map a year to a hex color using the year color scale."""
    if year is None:
        return _DEFAULT_YEAR_COLOR
    for lo, hi, color in _YEAR_COLOR_SCALE:
        if lo <= year <= hi:
            return color
    return _DEFAULT_YEAR_COLOR


# ─────────────────────────────────────────────────────────────────────────────
# GRAPH CONSTRUCTORS
# ─────────────────────────────────────────────────────────────────────────────

def build_label_label_graph(
    label_to_artists: dict[str, set[str]],
    min_shared: int = 1,
    label_names: dict[str, str] | None = None,
    seed_label_ids: list[str] | None = None,
    seed_artist_union: set[str] | None = None,
    min_overlap_pct: float = 0.0,
    label_years: dict[str, dict[str, int | None]] | None = None,
) -> nx.Graph:
    """
    Build a label-label graph where edges represent shared artist presence.

    Args:
        label_to_artists: Mapping of label_id → set of artist_ids.
                          Must be set-valued (not list-valued).
        min_shared:       Minimum number of shared artists required to
                          draw an edge between two labels.
        label_names:       Optional label_id → human-readable name mapping.
        seed_label_ids:    IDs of seed/input labels.  These are always added
                           as nodes (even if absent from label_to_artists).
        seed_artist_union: Pre-computed union of all artists across seed labels.
                           When provided, used directly for overlap calculation
                           instead of deriving it from label_to_artists (which
                           may not contain seed labels after filtering).

    Returns:
        Undirected graph with node attributes:
            kind     — "label"
            display  — human-readable label name
            overlap  — artists shared with the seed union (0 when no seeds)
            is_seed  — True when the node is a seed label
        and 'weight' edge attribute (shared-artist count).
    """
    G: nx.Graph = nx.Graph()

    # Merge seed labels into the working mapping so they always get nodes.
    effective_l2a: dict[str, set[str]] = dict(label_to_artists)
    seed_set: set[str] = {str(s) for s in (seed_label_ids or [])}
    for lid in seed_set:
        effective_l2a.setdefault(lid, set())

    labels = list(effective_l2a.keys())
    threshold = max(1, int(min_shared))

    # Seed union: use the pre-computed union if supplied (preferred — it is
    # derived from the unfiltered dataset so seed labels are always present).
    # Fall back to computing from effective_l2a when no union is provided.
    if seed_artist_union is not None:
        seed_union: set[str] = seed_artist_union
    else:
        seed_union = set()
        for lid in seed_set:
            seed_union |= effective_l2a.get(lid, set())

    # First pass: compute attributes and determine which labels pass the filter.
    # This must be done before adding any edges, because G.add_edge() auto-creates
    # nodes for endpoints that don't exist yet — without any attributes.  A label
    # that fails the overlap filter would be silently inserted as a bare node
    # (no 'display' attribute), causing draw_graph_matplotlib to fall back to
    # showing the raw label ID instead of the human-readable name.
    label_attrs: dict[str, dict] = {}
    for L in labels:
        is_seed = L in seed_set
        display = (label_names or {}).get(L) or L
        label_artists = effective_l2a[L]

        if is_seed:
            overlap_pct = 1.0
        else:
            overlap_count = len(label_artists & seed_union) if seed_union else 0
            overlap_pct = (overlap_count / len(seed_union)) if seed_union else 0.0
            overlap_pct = min(1.0, overlap_pct)

        if not is_seed and overlap_pct < min_overlap_pct:
            continue  # exclude — do not add node or any edges to/from it

        yrs = (label_years or {}).get(L, {})
        label_attrs[L] = {
            "kind":          "label",
            "display":       display,
            "overlap_pct":   overlap_pct,
            "is_seed":       is_seed,
            "earliest_year": yrs.get("earliest"),
            "latest_year":   yrs.get("latest"),
        }

    # Second pass: add nodes, then edges only between labels that both passed.
    for L, attrs in label_attrs.items():
        G.add_node(f"label:{L}", **attrs)

    for i, L in enumerate(labels):
        if L not in label_attrs:
            continue
        for j in range(i + 1, len(labels)):
            M = labels[j]
            if M not in label_attrs:
                continue
            shared = effective_l2a[L] & effective_l2a[M]
            if len(shared) >= threshold:
                G.add_edge(f"label:{L}", f"label:{M}", weight=len(shared))

    return G


def build_artist_label_graph(
    rows: list[dict],
    seed_label_ids: list[str] | None = None,
    label_names: dict[str, str] | None = None,
) -> nx.Graph:
    """
    Build a bipartite artist-label graph from release rows.

    Each row must contain 'artist_id' and 'label_id'.  Artist nodes have
    kind="artist"; label nodes have kind="label".

    Args:
        seed_label_ids: IDs of seed/input labels.  Always added as label nodes
                        (with is_seed=True) even when absent from rows.
        label_names:    Optional label_id → human-readable name mapping used
                        when a seed label has no rows and needs a display name.

    Returns:
        Undirected bipartite graph.
    """
    G: nx.Graph = nx.Graph()

    for r in rows:
        aid = r.get("artist_id")
        lid = r.get("label_id")
        if not aid or not lid:
            continue

        a_node = f"artist:{aid}"
        l_node = f"label:{lid}"

        # Store human-readable names as node attributes for label rendering.
        G.add_node(a_node, kind="artist", display=r.get("artist_name") or str(aid))
        G.add_node(l_node, kind="label",  display=r.get("label_name")  or str(lid),
                   is_seed=str(lid) in {str(s) for s in (seed_label_ids or [])})
        G.add_edge(a_node, l_node)

    # Ensure every seed label has a node even when no rows survived filtering.
    for lid in (seed_label_ids or []):
        lid_str = str(lid)
        l_node = f"label:{lid_str}"
        if l_node not in G:
            display = (label_names or {}).get(lid_str) or lid_str
            G.add_node(l_node, kind="label", display=display, is_seed=True)

    return G


# ─────────────────────────────────────────────────────────────────────────────
# RENDERING
# ─────────────────────────────────────────────────────────────────────────────

def draw_graph_matplotlib(G: nx.Graph, ax: plt.Axes | None = None) -> None:
    """
    Render a network graph onto a matplotlib Axes using a spring layout.

    Node appearance:
        Artists — small light-blue circles (size 60)
        Labels  — larger light-green circles (size 120)

    Node labels are displayed when the graph has 60 nodes or fewer to
    avoid an unreadable tangle on large graphs.  The "label:" / "artist:"
    prefix is stripped so only the meaningful identifier is shown.
    """
    ax = ax or plt.gca()

    pos = nx.spring_layout(G, seed=42, k=0.35)

    artist_nodes = [n for n, d in G.nodes(data=True) if d.get("kind") == "artist"]
    label_nodes  = [n for n, d in G.nodes(data=True) if d.get("kind") == "label"]

    # Year-based coloring: fill = latest release year, outline = earliest year.
    label_fill_colors = [
        _year_to_color(G.nodes[n].get("latest_year")) for n in label_nodes
    ]
    label_edge_colors = [
        _year_to_color(G.nodes[n].get("earliest_year")) for n in label_nodes
    ]

    # Step-function node sizing based on seed-artist overlap percentage.
    def _step_size(pct: float) -> float:
        if pct >= 0.50:
            return 1200.0
        if pct >= 0.25:
            return 800.0
        if pct >= 0.10:
            return 300.0
        if pct >= 0.05:
            return 150.0
        if pct >= 0.02:
            return 75.0
        return 38.0

    label_sizes = [_step_size(G.nodes[n].get("overlap_pct", 0.0)) for n in label_nodes]

    nx.draw_networkx_nodes(
        G, pos, nodelist=artist_nodes,
        node_size=60, node_color="lightblue", ax=ax,
    )
    nx.draw_networkx_nodes(
        G, pos, nodelist=label_nodes,
        node_size=label_sizes, node_color=label_fill_colors,
        edgecolors=label_edge_colors, linewidths=3.0, ax=ax,
    )
    nx.draw_networkx_edges(G, pos, alpha=0.3, ax=ax)

    # Compact year-color legend in the bottom-left corner.
    _legend_entries = []
    for lo, hi, color in _YEAR_COLOR_SCALE:
        lbl = str(lo) if lo == hi else (f"{lo}–{hi}" if lo > 0 else f"–{hi}")
        _legend_entries.append(
            mpatches.Patch(facecolor=color, edgecolor=color, label=lbl)
        )
    _legend_entries.append(
        mpatches.Patch(facecolor=_DEFAULT_YEAR_COLOR,
                       edgecolor=_DEFAULT_YEAR_COLOR, label="n/a")
    )
    leg = ax.legend(
        handles=_legend_entries,
        loc="center left",
        fontsize=5,
        framealpha=0.7,
        handlelength=1.0,
        handleheight=0.8,
        borderpad=0.4,
        labelspacing=0.25,
        title="Year",
        title_fontsize=6,
    )

    # Always draw labels; reduce font size for larger graphs.
    n_nodes = G.number_of_nodes()
    font_size = 7 if n_nodes <= 60 else 5 if n_nodes <= 120 else 4
    display_labels = {
        n: d.get("display") or n.split(":", 1)[-1]
        for n, d in G.nodes(data=True)
    }
    nx.draw_networkx_labels(
        G, pos, labels=display_labels,
        font_size=font_size, ax=ax,
    )

    ax.set_axis_off()
