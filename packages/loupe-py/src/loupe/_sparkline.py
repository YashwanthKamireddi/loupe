"""Tiny block-element sparkline renderer (pure stdlib).

Used by ``loupe cost`` and ``loupe stats`` to render a one-line trend
chart inline beside the summary totals. No dependencies — keeps the
visual footprint small and the install slim.

``▁▂▃▄▅▆▇█`` are the standard Unicode block elements; every modern
terminal renders them at fixed width with no font fiddling.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

_BLOCKS = " ▁▂▃▄▅▆▇█"  # 9 levels (0=empty space ... 8=full block)


def sparkline(values: Sequence[float], width: int | None = None) -> str:
    """Render ``values`` as a one-line block-element sparkline.

    Returns ``""`` if there are no values or every value is zero —
    callers can ``if spark:`` to decide whether to render at all.

    When ``width`` is given and there are more values than columns,
    the series is downsampled by averaging chunks.
    """
    if not values:
        return ""

    if width is not None and width > 0 and width < len(values):
        chunk = len(values) / width
        sampled: list[float] = []
        for i in range(width):
            lo = int(i * chunk)
            hi = max(lo + 1, int((i + 1) * chunk))
            window = list(values[lo:hi])
            sampled.append(sum(window) / len(window))
        values = sampled

    vmax = max(values)
    if vmax == 0:
        return ""

    levels = len(_BLOCKS) - 1  # 8 non-empty levels above the space
    return "".join(
        _BLOCKS[min(levels, int(round((v / vmax) * levels)))] for v in values
    )


def daily_series(
    items: Sequence[tuple[float, float]],
    days: int = 14,
) -> list[float]:
    """Bucket ``(epoch_seconds, value)`` pairs into ``days`` day-sized cells.

    Returns a list of length ``days``, oldest cell first and the cell
    containing today's UTC date last. Missing days are ``0.0``.

    Designed so that ``sparkline(daily_series(...))`` renders a clean
    14-day trend with the most-recent activity at the right edge —
    matches the reading direction in every CLI we benchmarked.
    """
    if days <= 0:
        return []

    today = datetime.now(tz=UTC).date()
    buckets = [0.0] * days
    for ts, val in items:
        if ts is None:
            continue
        try:
            d = datetime.fromtimestamp(float(ts), tz=UTC).date()
        except (OSError, OverflowError, ValueError):
            continue
        delta = (today - d).days
        if 0 <= delta < days:
            buckets[days - 1 - delta] += float(val)
    return buckets
