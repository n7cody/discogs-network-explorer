# Privacy Policy

**Discogs Network Explorer (dne/dnx)**
Last updated: 2026-05-21

## Overview

Discogs Network Explorer (dne) is an open-source, locally-run desktop application for exploring relationships between music labels and artists using the Discogs database. Its companion feature, dnx (Discogs Network Xtractor), builds playlists on YouTube and Apple Music from discovered music.

The application runs entirely on the user's own machine. There is no server, hosted service, or backend that collects, processes, or stores user data.

## Data accessed

### Discogs API
dne reads publicly available music metadata (artist names, label names, release titles, genres, years) from the Discogs API. This data is used locally to build network graphs and export reports. No data is written back to Discogs.

### YouTube Data API v3
dnx uses the YouTube Data API v3 to:
- **Search for videos** by artist and track name when search fallback is enabled (receives video IDs, video titles, and channel names from search results)
- **Create playlists** in the authenticated user's YouTube account (receives the created playlist ID)
- **Add videos** to those playlists (sends video IDs to the YouTube API)

YouTube API Data received by dnx (video IDs, video titles, channel names, playlist IDs) is used only within the current application session to build playlists. This data is not persisted to disk, transmitted to any external server, or shared with any third party. Once the application session ends, all in-memory API Data is discarded.

The only YouTube-related information persisted to disk is the OAuth 2.0 token described in the "Information stored on your device" section below.

dnx does **not** access, collect, or store YouTube watch history, subscriptions, likes, comments, uploaded videos, or any other YouTube account data beyond what is described above. dnx does not upload, modify, or delete any existing YouTube content.

### Apple Music (MusicKit API)
dnx optionally uses Apple's MusicKit API to search the Apple Music catalog and create playlists in the authenticated user's Apple Music library. No Apple Music listening history or account data is accessed.

## Information stored on your device

dne/dnx stores the following information locally on the user's machine. No data is transmitted to any server operated by the developer or any third party, other than the official API endpoints listed above (api.discogs.com, www.googleapis.com, api.music.apple.com).

- **YouTube OAuth 2.0 token** (`~/.dne/youtube_token.json`): Contains an access token and refresh token issued by Google during OAuth authorization. This file allows the application to maintain an authenticated session without requiring re-authorization on each use. It does not contain your Google account password.
- **Apple Music credentials** (`~/.dne/apple_music_config.json`, `~/.dne/apple_music_user_token.txt`): Contains developer and user tokens for Apple Music API access.
- **HTTP response cache** (`discogs_cache.sqlite`): A local SQLite database that caches API responses from Discogs to reduce redundant network requests. Contains publicly available music metadata only. Does not cache YouTube API responses or any YouTube user data.
- **Discogs API token** (`.env` file, optional): Stores the user's Discogs personal access token if configured.

dne/dnx does **not** use browser cookies, web beacons, tracking pixels, or similar browser-based tracking technology. No information is placed on or read from users' web browsers. All stored files reside in the local filesystem and are fully under the user's control.

No third party is permitted to access, collect, or store information on or from users' devices through dne/dnx.

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

dne/dnx's use of YouTube API Services is subject to the [YouTube Terms of Service](https://www.youtube.com/t/terms), [Google Privacy Policy](https://policies.google.com/privacy), and the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements. Users authenticate directly with Google via OAuth 2.0; dne/dnx never sees or stores Google account passwords.

## Contact

For questions about this privacy policy, open an issue at [github.com/n7cody/discogs-network-explorer](https://github.com/n7cody/discogs-network-explorer/issues).
