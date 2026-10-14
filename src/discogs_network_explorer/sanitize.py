"""Validation and escaping for user input and Discogs-supplied text.

Validators return the cleaned value or raise ValueError with a message that is
safe to show in the UI (secrets are never echoed back).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd

MAX_IDS = 500
MAX_ID_DIGITS = 12
MAX_TEXT = 200

# C0/C1 control characters and Unicode bidirectional overrides
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f‎‏‪-‮⁦-⁩]")
_VISIBLE_ASCII = re.compile(r"[\x21-\x7e]+")
_APPLE_ID = re.compile(r"[A-Za-z0-9]{10}")
_STOREFRONT = re.compile(r"[a-z]{2}")
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+\-.!|~$:])")
_CSV_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _preview(s: str, n: int = 30) -> str:
    s = _CONTROL.sub("", s)
    return s if len(s) <= n else s[:n] + "..."


def parse_ids(raw: str, what: str = "Discogs", max_count: int = MAX_IDS) -> list[str]:
    """Comma- or newline-separated positive integer IDs, deduplicated in input order."""
    ids: list[str] = []
    bad: list[str] = []
    for tok in raw.replace("\n", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok.isascii() and tok.isdigit() and len(tok) <= MAX_ID_DIGITS and int(tok) > 0:
            s = str(int(tok))
            if s not in ids:
                ids.append(s)
        else:
            bad.append(_preview(tok))
    if bad:
        raise ValueError(f"Invalid {what} ID(s), expected positive whole numbers: {', '.join(bad[:5])}")
    if len(ids) > max_count:
        raise ValueError(f"Too many {what} IDs: {len(ids)} (limit {max_count}).")
    return ids


def clean_text(s: str, max_len: int = MAX_TEXT, what: str = "Text") -> str:
    """Strip control characters and surrounding whitespace; reject text over `max_len`."""
    s = _CONTROL.sub("", s).strip()
    if len(s) > max_len:
        raise ValueError(f"{what} is too long: {len(s)} characters (limit {max_len}).")
    return s


def parse_names(raw: str, what: str = "Name", max_count: int = MAX_IDS,
                max_len: int = MAX_TEXT) -> list[str]:
    """Comma-separated names, cleaned and deduplicated in input order."""
    names: list[str] = []
    for tok in raw.split(","):
        tok = clean_text(tok, max_len, what)
        if tok and tok not in names:
            names.append(tok)
    if len(names) > max_count:
        raise ValueError(f"Too many {what.lower()}s: {len(names)} (limit {max_count}).")
    return names


def check_token(token: str, what: str, max_len: int = 200) -> str:
    """A credential string: visible ASCII only, bounded length. Never echoed in errors."""
    token = (token or "").strip()
    if not token:
        raise ValueError(f"{what} is required.")
    if len(token) > max_len or not _VISIBLE_ASCII.fullmatch(token):
        raise ValueError(f"{what} is malformed (unexpected characters or length).")
    return token


def check_apple_id(value: str, what: str) -> str:
    """Apple Team ID or Key ID: 10 letters or digits."""
    value = (value or "").strip()
    if not _APPLE_ID.fullmatch(value):
        raise ValueError(f"{what} must be exactly 10 letters or digits.")
    return value


def check_storefront(value: str) -> str:
    """ISO 3166-1 alpha-2 storefront code, lowercased."""
    value = (value or "").strip().lower()
    if not _STOREFRONT.fullmatch(value):
        raise ValueError("Storefront must be a two-letter country code, e.g. us, gb, de.")
    return value


def resolve_file(path: str, suffix: str, max_bytes: int) -> str:
    """Expand `~`, require an existing regular file with `suffix` no larger than `max_bytes`."""
    p = Path((path or "").strip()).expanduser()
    if p.suffix.lower() != suffix:
        raise ValueError(f"Expected a {suffix} file: {_preview(str(p), 120)}")
    try:
        p = p.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError(f"File not found: {_preview(str(p), 120)}") from None
    if not p.is_file():
        raise ValueError(f"Not a regular file: {p}")
    if p.stat().st_size > max_bytes:
        raise ValueError(f"File is larger than expected for a {suffix} file: {p}")
    return str(p)


def md_escape(s: object) -> str:
    """Backslash-escape Markdown (and Streamlit LaTeX / emoji) syntax in `s`."""
    return _MD_SPECIAL.sub(r"\\\1", str(s))


def csv_cell(value: object) -> object:
    """Prefix text that a spreadsheet would read as a formula with a single quote."""
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_START):
        return "'" + value
    return value


def csv_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Copy of `df` with formula-leading text cells and headers neutralised."""
    out = df.copy()
    for i, dtype in enumerate(out.dtypes):
        if dtype == object:
            out.isetitem(i, out.iloc[:, i].map(csv_cell))
    out.columns = [csv_cell(c) for c in out.columns]
    return out


def write_private(path: Path, text: str) -> None:
    """Write `text` to `path` readable only by the owner (file 0600, directory 0700)."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    # O_CREAT's mode applies only to new files
    os.chmod(path, 0o600)
