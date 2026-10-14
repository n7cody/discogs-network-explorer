import stat

import pandas as pd
import pytest

from discogs_network_explorer.backend import _id_path
from discogs_network_explorer.ingest.cli import main as cli_main
from discogs_network_explorer.ingest.update import UpdateError, run_update
from discogs_network_explorer.sanitize import (
    check_apple_id,
    check_storefront,
    check_token,
    clean_text,
    csv_cell,
    csv_safe,
    md_escape,
    parse_ids,
    parse_names,
    resolve_file,
    write_private,
)


def test_parse_ids_accepts_commas_newlines_and_dedupes():
    assert parse_ids("3661094, 1390196\n3661094\n\n007") == ["3661094", "1390196", "7"]
    assert parse_ids("  ") == []


@pytest.mark.parametrize("raw", ["12a", "-5", "0", "1/../2", "1?x=1", "１２", "1" * 13, "1.5"])
def test_parse_ids_rejects_non_ids(raw):
    with pytest.raises(ValueError, match="Invalid"):
        parse_ids(raw, "label")


def test_parse_ids_bounded_count():
    with pytest.raises(ValueError, match="Too many"):
        parse_ids(",".join(str(i) for i in range(1, 12)), max_count=10)


def test_clean_text_strips_controls_and_bounds_length():
    assert clean_text("  a\x00b‮c\n ") == "abc"
    with pytest.raises(ValueError, match="too long"):
        clean_text("x" * 11, max_len=10)


def test_parse_names():
    assert parse_names("Livity Sound, NAFF,, Livity Sound") == ["Livity Sound", "NAFF"]
    with pytest.raises(ValueError):
        parse_names("x" * 300)


def test_check_token_never_echoes_secret():
    assert check_token(" abcDEF123 \n", "Token") == "abcDEF123"
    with pytest.raises(ValueError, match="required"):
        check_token("  ", "Token")
    with pytest.raises(ValueError) as e:
        check_token("secret value\r\nX-Injected: 1", "Token")
    assert "secret" not in str(e.value)
    with pytest.raises(ValueError):
        check_token("a" * 201, "Token")


def test_check_apple_id_and_storefront():
    assert check_apple_id(" ABCDE12345 ", "Team ID") == "ABCDE12345"
    for bad in ["", "ABCDE1234", "ABCDE12345X", "ABCDE/2345"]:
        with pytest.raises(ValueError):
            check_apple_id(bad, "Team ID")
    assert check_storefront(" GB ") == "gb"
    for bad in ["", "usa", "u1", "../"]:
        with pytest.raises(ValueError):
            check_storefront(bad)


def test_resolve_file(tmp_path, monkeypatch):
    key = tmp_path / "AuthKey_ABC.p8"
    key.write_text("-----BEGIN PRIVATE KEY-----\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_file("~/AuthKey_ABC.p8", ".p8", 1000) == str(key.resolve())
    with pytest.raises(ValueError, match="Expected a .p8"):
        resolve_file(str(tmp_path / "id_rsa"), ".p8", 1000)
    with pytest.raises(ValueError, match="not found"):
        resolve_file(str(tmp_path / "missing.p8"), ".p8", 1000)
    (tmp_path / "dir.p8").mkdir()
    with pytest.raises(ValueError, match="regular file"):
        resolve_file(str(tmp_path / "dir.p8"), ".p8", 1000)
    with pytest.raises(ValueError, match="larger"):
        resolve_file(str(key), ".p8", 5)


def test_md_escape():
    assert md_escape("[x](javascript:alert(1))") == r"\[x\]\(javascript\:alert\(1\)\)"
    assert md_escape("Ca$h **bold**") == r"Ca\$h \*\*bold\*\*"


def test_csv_safe_neutralises_formulas():
    df = pd.DataFrame({
        "name": ["=HYPERLINK(\"http://x\")", "+1", "-A", "@SUM(A1)", "\tx", "Plain"],
        "n": [1, 2, 3, 4, 5, 6],
        "=hdr": ["a"] * 6,
    })
    out = csv_safe(df)
    assert list(out["name"]) == ["'=HYPERLINK(\"http://x\")", "'+1", "'-A", "'@SUM(A1)", "'\tx", "Plain"]
    assert list(out["n"]) == [1, 2, 3, 4, 5, 6]
    assert list(out.columns) == ["name", "n", "'=hdr"]
    assert df["name"][0].startswith("=")
    assert csv_cell(None) is None


def test_write_private(tmp_path):
    path = tmp_path / "cfg" / "token.json"
    write_private(path, "{}")
    assert path.read_text() == "{}"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    path.chmod(0o644)
    write_private(path, "{\"a\": 1}")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_backend_id_path():
    assert _id_path(" 123 ") == "123"
    for bad in ["12/../users", "1?q=2", "", "abc"]:
        with pytest.raises(ValueError):
            _id_path(bad)


def test_cli_rejects_bad_limit():
    with pytest.raises(SystemExit):
        cli_main(["--limit", "0"])


def test_run_update_rejects_bad_dump_date(tmp_path):
    with pytest.raises(UpdateError, match="YYYYMMDD"):
        run_update(tmp_path, source=None, dump="../../etc")
