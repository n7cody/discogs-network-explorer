"""
apple_music.py — Apple Music API (MusicKit) helpers for dnx.

Handles JWT developer token generation, user authentication via Music User Token,
song search, playlist creation, and adding tracks to playlists.

Requires an Apple Developer account with a MusicKit key:
  - Team ID
  - Key ID
  - Private key (.p8 file)

See apple_music_setup.txt for setup instructions.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import jwt
import requests

# Apple Music API base URL.
_API_BASE = "https://api.music.apple.com/v1"

# Credential storage.
_CONFIG_DIR = Path.home() / ".dne"
_AM_CONFIG_PATH = _CONFIG_DIR / "apple_music_config.json"
_AM_USER_TOKEN_PATH = _CONFIG_DIR / "apple_music_user_token.txt"


def _ensure_config_dir() -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def get_stored_config_path() -> Path:
    return _AM_CONFIG_PATH


def get_stored_user_token_path() -> Path:
    return _AM_USER_TOKEN_PATH


# ── Developer token (JWT) ──────────────────────────────────────────────────


def generate_developer_token(
    team_id: str,
    key_id: str,
    private_key_path: str,
) -> str:
    """
    Generate a signed JWT developer token for the Apple Music API.

    Args:
        team_id:          Apple Developer Team ID (10-char alphanumeric).
        key_id:           MusicKit Key ID (10-char alphanumeric).
        private_key_path: Path to the .p8 private key file downloaded from
                          Apple Developer portal.

    Returns:
        Signed JWT string valid for 6 months (max allowed by Apple).
    """
    private_key = Path(private_key_path).read_text()

    now = int(time.time())
    payload = {
        "iss": team_id,
        "iat": now,
        "exp": now + (6 * 30 * 24 * 60 * 60),  # ~6 months
    }
    headers = {
        "alg": "ES256",
        "kid": key_id,
    }

    token = jwt.encode(payload, private_key, algorithm="ES256", headers=headers)
    return token


def save_config(team_id: str, key_id: str, private_key_path: str) -> str:
    """
    Save Apple Music developer credentials and generate a developer token.

    Returns the developer token.
    """
    _ensure_config_dir()
    dev_token = generate_developer_token(team_id, key_id, private_key_path)
    config = {
        "team_id": team_id,
        "key_id": key_id,
        "private_key_path": private_key_path,
        "developer_token": dev_token,
    }
    _AM_CONFIG_PATH.write_text(json.dumps(config, indent=2))
    return dev_token


def load_config() -> dict | None:
    """
    Load saved Apple Music config from disk.

    Returns dict with team_id, key_id, private_key_path, developer_token,
    or None if not configured.
    """
    if not _AM_CONFIG_PATH.exists():
        return None
    try:
        config = json.loads(_AM_CONFIG_PATH.read_text())
        # Verify the token hasn't expired.
        dev_token = config.get("developer_token", "")
        decoded = jwt.decode(dev_token, options={"verify_signature": False})
        if decoded.get("exp", 0) < time.time():
            # Token expired — regenerate.
            dev_token = generate_developer_token(
                config["team_id"],
                config["key_id"],
                config["private_key_path"],
            )
            config["developer_token"] = dev_token
            _AM_CONFIG_PATH.write_text(json.dumps(config, indent=2))
        return config
    except Exception:
        return None


def save_user_token(user_token: str) -> None:
    """Save the Music User Token (obtained from MusicKit JS or manual entry)."""
    _ensure_config_dir()
    _AM_USER_TOKEN_PATH.write_text(user_token.strip())


def load_user_token() -> str | None:
    """Load saved Music User Token, or None if not set."""
    if not _AM_USER_TOKEN_PATH.exists():
        return None
    token = _AM_USER_TOKEN_PATH.read_text().strip()
    return token if token else None


def clear_credentials() -> None:
    """Remove all saved Apple Music credentials."""
    if _AM_CONFIG_PATH.exists():
        _AM_CONFIG_PATH.unlink()
    if _AM_USER_TOKEN_PATH.exists():
        _AM_USER_TOKEN_PATH.unlink()


def is_connected() -> bool:
    """Check if both developer token and user token are available."""
    config = load_config()
    user_token = load_user_token()
    return config is not None and user_token is not None


# ── API helpers ─────────────────────────────────────────────────────────────


def _auth_headers(developer_token: str, user_token: str) -> dict:
    return {
        "Authorization": f"Bearer {developer_token}",
        "Music-User-Token": user_token,
        "Content-Type": "application/json",
    }


def _dev_headers(developer_token: str) -> dict:
    return {
        "Authorization": f"Bearer {developer_token}",
        "Content-Type": "application/json",
    }


def search_song(
    developer_token: str,
    query: str,
    storefront: str = "us",
) -> dict | None:
    """
    Search Apple Music for a song.

    Args:
        developer_token: JWT developer token.
        query:           Search string (e.g. "Artist - Title").
        storefront:      ISO 3166-1 alpha-2 country code (default: "us").

    Returns:
        Dict with 'song_id', 'title', 'artist', or None if no result.
    """
    resp = requests.get(
        f"{_API_BASE}/catalog/{storefront}/search",
        headers=_dev_headers(developer_token),
        params={
            "term": query,
            "types": "songs",
            "limit": 1,
        },
    )
    resp.raise_for_status()
    data = resp.json()

    songs = data.get("results", {}).get("songs", {}).get("data", [])
    if not songs:
        return None

    song = songs[0]
    attrs = song.get("attributes", {})
    return {
        "song_id": song["id"],
        "title": attrs.get("name", ""),
        "artist": attrs.get("artistName", ""),
    }


def create_playlist(
    developer_token: str,
    user_token: str,
    title: str,
    description: str = "",
) -> str:
    """
    Create a new playlist in the user's Apple Music library.

    Returns the playlist ID (library playlist ID).
    """
    resp = requests.post(
        f"{_API_BASE}/me/library/playlists",
        headers=_auth_headers(developer_token, user_token),
        json={
            "attributes": {
                "name": title,
                "description": description,
            },
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return data["data"][0]["id"]


def add_songs_to_playlist(
    developer_token: str,
    user_token: str,
    playlist_id: str,
    song_ids: list[str],
) -> None:
    """
    Add songs to an existing playlist.

    Apple Music allows batch adding — we send all song IDs at once.

    Args:
        developer_token: JWT developer token.
        user_token:      Music User Token.
        playlist_id:     Library playlist ID.
        song_ids:        List of catalog song IDs.
    """
    tracks = [{"id": sid, "type": "songs"} for sid in song_ids]
    resp = requests.post(
        f"{_API_BASE}/me/library/playlists/{playlist_id}/tracks",
        headers=_auth_headers(developer_token, user_token),
        json={"data": tracks},
    )
    resp.raise_for_status()
