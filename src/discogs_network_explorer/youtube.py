"""
youtube.py — YouTube Data API v3 helpers for dnx (Discogs Network Xtractor).

Handles OAuth 2.0 authentication, video search, playlist creation, and
adding videos to playlists.  Credentials are cached to disk so the user
only needs to authorize once.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# YouTube API scope — manage playlists and read-only search.
_SCOPES = ["https://www.googleapis.com/auth/youtube"]

# Default paths for credential storage.
_CONFIG_DIR = Path.home() / ".dne"
_TOKEN_PATH = _CONFIG_DIR / "youtube_token.json"

# Quota costs (YouTube Data API v3):
#   search.list  = 100 units
#   playlists.insert = 50 units
#   playlistItems.insert = 50 units
# Daily quota = 10,000 units → ~90 searches + 1 playlist + 90 adds per day.


def _ensure_config_dir() -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def get_stored_token_path() -> Path:
    return _TOKEN_PATH


def authenticate(client_secret_path: str) -> Credentials:
    """
    Run the OAuth 2.0 installed-app flow.

    Opens the user's browser for Google authorization.  The resulting
    token is saved to ~/.dne/youtube_token.json for reuse.

    Args:
        client_secret_path: Path to the OAuth client_secret JSON downloaded
                            from Google Cloud Console.

    Returns:
        Authorized Credentials object.
    """
    flow = InstalledAppFlow.from_client_secrets_file(
        client_secret_path, scopes=_SCOPES
    )
    creds = flow.run_local_server(port=0, open_browser=True)
    _save_token(creds)
    return creds


def load_credentials() -> Credentials | None:
    """
    Load saved credentials from disk.

    Returns None if no saved token exists or the token is expired with
    no refresh token available.
    """
    if not _TOKEN_PATH.exists():
        return None

    creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _SCOPES)

    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        _save_token(creds)

    if not creds.valid:
        return None

    return creds


def _save_token(creds: Credentials) -> None:
    _ensure_config_dir()
    _TOKEN_PATH.write_text(creds.to_json())


def clear_credentials() -> None:
    """Remove saved YouTube credentials."""
    if _TOKEN_PATH.exists():
        _TOKEN_PATH.unlink()


def get_youtube_service(creds: Credentials):
    """Build and return an authorized YouTube API service object."""
    return build("youtube", "v3", credentials=creds)


def search_video(service, query: str) -> dict | None:
    """
    Search YouTube for a single video matching the query.

    Returns a dict with 'video_id' and 'title', or None if no result.
    """
    resp = (
        service.search()
        .list(part="snippet", q=query, type="video", maxResults=1)
        .execute()
    )
    items = resp.get("items", [])
    if not items:
        return None
    item = items[0]
    return {
        "video_id": item["id"]["videoId"],
        "title": item["snippet"]["title"],
    }


def create_playlist(
    service,
    title: str,
    description: str = "",
) -> str:
    """
    Create a new YouTube playlist.

    Returns the playlist ID.
    """
    resp = (
        service.playlists()
        .insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title,
                    "description": description,
                },
                "status": {"privacyStatus": "unlisted"},
            },
        )
        .execute()
    )
    return resp["id"]


def add_video_to_playlist(service, playlist_id: str, video_id: str) -> None:
    """Add a video to a playlist by ID."""
    service.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id,
                },
            }
        },
    ).execute()
