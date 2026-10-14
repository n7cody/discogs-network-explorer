import pandas as pd
import pytest

from discogs_network_explorer import filters as F
from discogs_network_explorer.filters import (
    exclude_dj_mixes,
    filter_by_input_overlap,
    input_artist_pools,
    input_overlap,
)


def _rows(*triples, col="format_descriptions"):
    return pd.DataFrame(
        [{"label_id": l, "artist_id": a, col: f} for l, a, f in triples]
    )


def test_exclude_dj_mixes_drops_mixed_rows_only():
    df = _rows(("1", "a", "12\", Mixed"), ("1", "b", "Compilation"), ("2", "c", "Mixed Media"))
    assert exclude_dj_mixes(df)["artist_id"].tolist() == ["b", "c"]


def test_exclude_dj_mixes_reads_api_format_string():
    df = _rows(("1", "a", "File, MP3, Mixed"), ("1", "b", "Vinyl, 12\""), col="format")
    assert exclude_dj_mixes(df)["artist_id"].tolist() == ["b"]


# Inputs: A has artists a1..a4 (big), B has b1 (small).
RAW = _rows(*[("A", a, "") for a in ("a1", "a2", "a3", "a4")], ("B", "b1", ""),
            ("X", "a1", ""), ("X", "a2", ""),   # X: half of A, none of B
            ("Y", "a1", ""), ("Y", "b1", ""))   # Y: a quarter of A, all of B
POOLS = input_artist_pools(RAW, ["A", "B"], [], {"a1", "a2", "a3", "a4", "b1"})


def test_pools_per_input_label():
    assert POOLS == {"A": {"a1", "a2", "a3", "a4"}, "B": {"b1"}}


def test_pools_per_input_artist_without_input_labels():
    assert input_artist_pools(RAW, [], ["a1", "b1"], set()) == {"a1": {"a1"}, "b1": {"b1"}}


def test_weighted_overlap_averages_each_input_share():
    ov = input_overlap(RAW, POOLS).set_index("label_id")
    assert ov.loc["X", "weighted_overlap_pct"] == pytest.approx(25.0)    # (50 + 0) / 2
    assert ov.loc["Y", "weighted_overlap_pct"] == pytest.approx(62.5)    # (25 + 100) / 2
    assert ov.loc["X", "inputs_reached"] == 1
    assert ov.loc["Y", "inputs_reached"] == 2


def test_filter_by_input_overlap_keeps_inputs():
    out = filter_by_input_overlap(RAW, POOLS, min_inputs_reached=2, keep_label_ids=["A", "B"])
    assert set(out["label_id"]) == {"A", "B", "Y"}
    out = filter_by_input_overlap(RAW, POOLS, min_weighted_pct=30, keep_label_ids=["A", "B"])
    assert set(out["label_id"]) == {"A", "B", "Y"}


# Empty frames keep their columns (pandas reads df[<empty object Series>] as a column selection)

COLS = ["artist_id", "label_id", "label_name", "release_id", "year", "format", "format_descriptions",
        "country", "role", "genres", "styles"]
EMPTY = pd.DataFrame({c: pd.Series(dtype=object) for c in COLS})

EMPTY_CASES = {
    "year": lambda df: F.filter_by_year_range(df, 2000, 2010),
    "format": lambda df: F.filter_by_format(df, {"Vinyl"}),
    "country": lambda df: F.filter_by_country(df, {"UK"}),
    "role": lambda df: F.filter_by_role(df, {"Main"}),
    "genres": lambda df: F.filter_by_genres(df, {"Electronic"}),
    "styles": lambda df: F.filter_by_styles(df, {"House"}),
    "size": lambda df: F.filter_labels_by_size(df, 2, 5),
    "label_only": lambda df: F.apply_label_only_mode(df, ["1"], 1, {"a"}),
    "label_plus_artist": lambda df: F.apply_label_plus_artist_mode(df, ["1"], ["a"]),
    "artists_only": lambda df: F.apply_artists_only_mode(df, ["a"], 1),
    "label_window": lambda df: F.filter_labels_by_activity_window(df, 2000, 2010),
    "artist_window": lambda df: F.filter_artists_by_activity_window(df, 2000, 2010),
    "min_artist_releases": lambda df: F.filter_by_min_artist_releases(df, 2),
    "dj_mixes": F.exclude_dj_mixes,
    "input_overlap": lambda df: F.filter_by_input_overlap(df, {"1": {"a"}}, min_inputs_reached=2),
}


@pytest.mark.parametrize("name", EMPTY_CASES)
def test_empty_frame_keeps_columns(name):
    out = EMPTY_CASES[name](EMPTY)
    assert out.empty
    assert list(out.columns) == COLS


@pytest.mark.parametrize("name", EMPTY_CASES)
def test_filter_that_empties_keeps_columns(name):
    df = pd.DataFrame([{"artist_id": "z", "label_id": "9", "label_name": "L", "release_id": "r",
                        "year": 1990, "format": "CD", "format_descriptions": "Mixed",
                        "country": "US", "role": "Track", "genres": "Rock", "styles": "Punk"}])
    out = EMPTY_CASES[name](df)
    assert out.empty
    assert list(out.columns) == COLS


def test_artists_only_mode_on_empty_frame_from_a_previous_filter():
    df = F.exclude_dj_mixes(EMPTY)
    out = F.apply_artists_only_mode(df, ["a"], 1)
    assert list(out.columns) == COLS
    assert out["label_id"].tolist() == []
