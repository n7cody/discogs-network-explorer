# Privacy Policy

**Discogs Network Explorer (dne/dnx)**
Last updated: 2026-04-30

## Overview

Discogs Network Explorer (dne) is an open-source, locally-run desktop application for exploring relationships between music labels and artists using the Discogs database. Its companion feature, dnx (Discogs Network Xtractor), builds playlists on YouTube and Apple Music from discovered music.

The application runs entirely on the user's own machine. There is no server, hosted service, or backend that collects, processes, or stores user data.

## Data accessed

### Discogs API
dne reads publicly available music metadata (artist names, label names, release titles, genres, years) from the Discogs API. This data is used locally to build network graphs and export reports. No data is written back to Discogs.

### YouTube Data API v3
dnx uses the YouTube Data API v3 to:
- **Create playlists** in the authenticated user's own YouTube account
- **Add videos** to those playlists
- **Search** for videos by artist and track name (when search fallback is enabled)

dnx does **not** upload, modify, or delete any existing YouTube content. It does not access watch history, subscriptions, comments, or any other YouTube account data beyond playlist creation.

### Apple Music (MusicKit API)
dnx optionally uses Apple's MusicKit API to search the Apple Music catalog and create playlists in the authenticated user's Apple Music library. No Apple Music listening history or account data is accessed.

## Data storage

All data is stored locally on the user's machine:

- **OAuth tokens** (YouTube): Saved to `~/.dne/youtube_token.json` on the user's local filesystem. Used to maintain the YouTube API session without requiring re-authorization on each use.
- **Apple Music credentials**: Saved to `~/.dne/apple_music_config.json` and `~/.dne/apple_music_user_token.txt` on the user's local filesystem.
- **HTTP cache**: Optionally stored in a local SQLite file (`discogs_cache.sqlite`) to avoid redundant API calls. Contains only publicly available Discogs metadata.
- **Discogs token**: Optionally stored in a local `.env` file.

No data is transmitted to any server operated by the developer or any third party, other than the official API endpoints listed above (api.discogs.com, www.googleapis.com, api.music.apple.com).

## Data sharing

dne/dnx does **not**:
- Collect, transmit, or store any user data on external servers
- Share data with third parties
- Use analytics, tracking, or telemetry of any kind
- Display advertisements

## User control

- Users can disconnect YouTube or Apple Music at any time from the app interface
- Users can delete stored credentials by removing the `~/.dne/` directory
- Users can clear the HTTP cache from the app sidebar
- Users can revoke OAuth access from their [Google Account permissions page](https://myaccount.google.com/permissions)

## YouTube API Services

dne/dnx's use of YouTube API Services is subject to [Google's Privacy Policy](https://policies.google.com/privacy) and the [YouTube Terms of Service](https://www.youtube.com/t/terms). Users authenticate directly with Google via OAuth 2.0; dne/dnx never sees or stores Google account passwords.

## Contact

For questions about this privacy policy, open an issue at [github.com/n7cody/discogs-network-explorer](https://github.com/n7cody/discogs-network-explorer/issues).
