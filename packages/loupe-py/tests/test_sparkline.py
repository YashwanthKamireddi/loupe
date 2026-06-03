"""Tests for the inline sparkline renderer used by cost / stats."""

from __future__ import annotations

import time

from loupe._sparkline import daily_series, sparkline

_BLOCKS = " ▁▂▃▄▅▆▇█"


def test_empty_input_renders_empty_string() -> None:
    assert sparkline([]) == ""


def test_all_zero_renders_empty_string() -> None:
    # Render-nothing rather than a row of empty spaces — callers branch on
    # truthiness to decide whether to add a "14-day" row at all.
    assert sparkline([0, 0, 0]) == ""


def test_uses_only_block_glyphs() -> None:
    valid = set(_BLOCKS)
    out = sparkline([1, 5, 3, 9, 2, 7, 4])
    assert out
    assert all(ch in valid for ch in out)


def test_max_value_renders_full_block() -> None:
    out = sparkline([1, 2, 4, 8])
    assert out[-1] == "█"


def test_constant_series_renders_all_full() -> None:
    # Every value equals the max, so every cell hits the top level.
    assert sparkline([5, 5, 5]) == "███"


def test_length_matches_input_when_under_width() -> None:
    assert len(sparkline([1, 2, 3])) == 3


def test_width_larger_than_input_does_not_pad() -> None:
    # When the series is shorter than the requested width, render as-is
    # rather than padding — pads would lie about how much data we have.
    assert len(sparkline([1, 2, 3], width=10)) == 3


def test_downsampled_to_requested_width() -> None:
    out = sparkline(list(range(100)), width=10)
    assert len(out) == 10
    # The downsampled last cell averages the top of the range; it should
    # remain the largest cell, hence render as a full block.
    assert out[-1] == "█"


def test_daily_series_buckets_into_n_cells() -> None:
    now = time.time()
    items = [(now, 5.0), (now - 86400, 3.0), (now - 86400 * 13, 1.0)]
    out = daily_series(items, days=14)
    assert len(out) == 14
    # The most-recent timestamp lands in the last cell.
    assert out[-1] == 5.0
    # Yesterday's timestamp lands in the second-to-last cell.
    assert out[-2] == 3.0
    # The earliest timestamp lands in the first cell.
    assert out[0] == 1.0


def test_daily_series_drops_out_of_window() -> None:
    now = time.time()
    items = [(now, 5.0), (now - 86400 * 30, 999.0)]  # 30 days ago is outside 14
    out = daily_series(items, days=14)
    assert out[-1] == 5.0
    assert sum(out) == 5.0  # the 999 was dropped


def test_daily_series_handles_invalid_timestamps() -> None:
    # None / unparseable timestamps must not crash; they're silently skipped.
    out = daily_series([(None, 5.0), ("oops", 3.0), (time.time(), 1.0)], days=7)  # type: ignore[list-item]
    assert out[-1] == 1.0


def test_daily_series_with_zero_days_is_empty() -> None:
    assert daily_series([(time.time(), 5.0)], days=0) == []


def test_sparkline_of_daily_series_end_to_end() -> None:
    # Realistic shape: a few days of light activity, today spike.
    now = time.time()
    items = [(now - 86400 * d, float(d)) for d in range(7)]
    out = sparkline(daily_series(items, days=7))
    assert len(out) == 7
    # Most-recent day has value 0 (d=0 in the comprehension), oldest has 6.
    assert out[0] == "█"
    assert out[-1] == " "
