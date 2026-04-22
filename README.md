# dne — Discogs Network Explorer

![dne app screenshot](dne_app.png)

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
3. **Discovered Labels** — one row per label with artist names, total Discogs release count, overlap percentage, earliest/latest year, and label ID

## dnx — Discogs Network Xtractor

**dnx** builds playlists on **YouTube** or **Apple Music** from labels or artists discovered during an analysis. A platform selector at the top of the dnx section lets you choose your target.

- **YouTube** — extracts community-curated YouTube links embedded on Discogs release pages. No YouTube API cost to discover most tracks. Search fallback (off by default) finds tracks for releases without embedded links.
- **Apple Music** — all tracks go through Apple Music catalog search (Discogs has no Apple Music embeds). Search fallback defaults to ON.

dnx appears at the bottom of the app after running an analysis.

### Match quality scoring

When dnx searches YouTube or Apple Music for tracks, each result is scored by comparing the Discogs artist name and track title against the platform's returned match:

| Verdict | Criteria | Action |
|---------|----------|--------|
| **Accept** | Artist similarity >= 0.7 and track similarity >= 0.5 | Added to playlist automatically |
| **Borderline** | Artist similarity >= 0.5 but below accept threshold | Shown in a review table with checkboxes |
| **Reject** | Artist similarity < 0.5 | Discarded (shown in collapsed expander) |

Borderline tracks default to unchecked — you opt in. Accepted borderline tracks are re-inserted at their original position so the playlist stays in release order.

### YouTube API setup (one-time)

dnx uses the YouTube Data API v3 to create playlists. You need a free Google Cloud project:

1. **Create a Google Cloud project**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Click the project dropdown at the top → **New Project** → give it any name (e.g. "DNX") → **Create**

2. **Enable the YouTube Data API v3**
   - Go to [APIs & Services → Library](https://console.cloud.google.com/apis/library)
   - Search for **YouTube Data API v3** → click it → **Enable**

3. **Configure the OAuth consent screen**
   - Go to [APIs & Services → OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)
   - Choose **External** → **Create**
   - Fill in the required fields (app name, your email for support contact and developer contact) — other fields can be left blank
   - Skip the **Scopes** step (click Save and Continue)
   - Go to the **Audience** tab (left sidebar) → under **Test users**, click **Add Users** and enter your own Google/Gmail email address
   - Click **Save and Continue** → **Back to Dashboard**

4. **Create OAuth credentials**
   - Go to [APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)
   - Click **Create Credentials** → **OAuth client ID**
   - Application type: **Desktop app**
   - Name: anything (e.g. "dnx")
   - Click **Create**
   - Click **Download JSON** on the confirmation dialog
   - Save the file as `~/client_secret.json` (or any path — you'll enter the path in the app)

5. **Connect in the app**
   - In the dnx section, enter the path to your `client_secret.json`
   - Click **Connect YouTube** — your browser will open for Google authorization
   - Authorize the app — the token is saved to `~/.dne/youtube_token.json` for future sessions

### YouTube API quota

The free daily quota is 10,000 units. dnx primarily uses Discogs-embedded video links (zero YouTube API cost to discover). The only API calls are playlist operations:

| Operation | Cost |
|-----------|------|
| Create playlist | 50 units |
| Add video to playlist | 50 units per video |
| YouTube search (fallback, off by default) | 100 units per search |

A typical run adding 100 videos costs ~5,050 units — well within the daily limit.

### Apple Music setup (one-time)

dnx uses Apple's MusicKit API. You need a paid [Apple Developer Program](https://developer.apple.com/programs/) membership ($99/year).

1. **Register a Media ID**
   - Sign in to [Apple Developer](https://developer.apple.com/account) → Certificates, Identifiers & Profiles → **Identifiers**
   - Click **"+"** → select **Media IDs** → Continue
   - Enter a description (e.g. "dnx") and identifier (e.g. `com.yourname.dnx`)
   - Check **MusicKit** → Continue → Register

2. **Create a MusicKit key**
   - Go to **Keys** → click **"+"**
   - Check **MusicKit**, associate it with the Media ID you just created, give it a name (e.g. "dnx")
   - Download the `.p8` private key file (you can only download it once)
   - Note your **Team ID** (top-right of the portal, 10 characters) and **Key ID** (shown on the key details page, 10 characters)

3. **Get a Music User Token**
   - Generate a developer token (JWT) in Python:
     ```python
     from discogs_network_explorer.apple_music import generate_developer_token
     print(generate_developer_token("YOUR_TEAM_ID", "YOUR_KEY_ID", "~/AuthKey_XXXXXX.p8"))
     ```
   - Save the HTML page from `apple_music_setup.txt`, paste your developer token into the `DEVELOPER_TOKEN` line
   - Serve the file over localhost (`python3 -m http.server 8080`) and open it in **Safari**
   - Click Authorize, sign in with your Apple ID, and copy the Music User Token

4. **Connect in the app**
   - In the dnx section, select **Apple Music**
   - Enter your Team ID, Key ID, path to `.p8` file, and Music User Token
   - Click **Connect Apple Music** — credentials are saved to `~/.dne/` for future sessions

### Apple Music notes

- The developer token auto-regenerates from your `.p8` key when it expires (~6 months)
- The Music User Token may expire — re-authorize via the HTML page if playlist creation fails
- Apple Music catalog search has no per-query quota limit
- The storefront country code should match your Apple Music subscription region (e.g. `us`, `gb`, `de`)

## HTTP cache

Enable **Cache HTTP responses** in the sidebar to store API responses in `discogs_cache.sqlite`. Subsequent runs reuse cached data, making re-analysis near-instant. Clear the cache from the sidebar when you want fresh data.

## Development

```bash
pip install -e .      # editable install — changes to src/ take effect immediately
dne                   # run the app
```
