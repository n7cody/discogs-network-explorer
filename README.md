# Discogs Network Explorer

A Streamlit app for exploring relationships between Discogs labels and artists. Crawls the Discogs API to discover which labels share rosters, visualises the network as an interactive graph, and exports results as a multi-sheet Excel report.

## Requirements

- Python 3.10 or newer
- A [Discogs personal access token](https://www.discogs.com/settings/developers) (free account required)

## Installation

```bash
git clone https://github.com/n7cody/discogs-network-explorer.git
cd discogs-network-explorer
pip install -e .
```

## Running the app

```bash
dne
```

This opens the app in your browser. Paste your Discogs token into the **Authentication** field in the sidebar before running any analysis.

Optionally store your token in a `.env` file at the repo root so you do not have to paste it each session:

```
DISCOGS_TOKEN=your_token_here
```

## Seed modes

| Mode | What it does |
|------|-------------|
| **Labels Only** | Finds all labels that share artists with one or more seed labels |
| **Labels + Artists** | Requires every discovered label to also contain specific seed artists |
| **Artists Only** | Finds labels that share a minimum number of artists from an explicit artist ID list |

## Discovery settings (sidebar)

| Setting | Description |
|---------|-------------|
| Year range | Restrict releases to a date window |
| Max releases per label | How many releases to crawl per label (default 40) |
| Max releases per artist | How many releases to crawl per discovered artist (default 40) |
| Min releases per label | Drop labels with fewer than N total releases in the dataset |
| Min releases per artist | Drop artists with fewer than N total releases in the dataset |
| Release Activity Window | Require labels/artists to have releases within a specific year span |
| Max global releases | Skip labels whose total Discogs catalogue exceeds this threshold |

## Network graph

Two graph types are available via **Graph Options** in the sidebar:

- **Label → Label** — undirected graph where edges connect labels sharing at least N artists
- **Artist → Label** — bipartite graph showing which artists appear on which labels

## Excel export

The **Results** tab produces a `.xlsx` file with three sheets:

1. **Run Info** — seed configuration, search parameters, and the discovered/input artist list
2. **All Releases** — every release row pulled during the crawl
3. **Discovered Labels** — one row per label with artist names, overlap percentage, and label ID

## HTTP cache

Enable **Cache HTTP responses** in the sidebar to store API responses in `discogs_cache.sqlite`. Subsequent runs reuse cached data, making re-analysis near-instant. Clear the cache from the sidebar when you want fresh data.

## Development

```bash
pip install -e .      # editable install — changes to src/ take effect immediately
dne                   # run the app
```
