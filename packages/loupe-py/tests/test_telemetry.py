"""Internal telemetry — observable but non-throwing failure surface."""

from __future__ import annotations

import warnings

from loupe._telemetry import LoupeTelemetryWarning, call_safe, emit, shielded


def test_emit_raises_a_loupe_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", LoupeTelemetryWarning)
        emit("test_site", ValueError("boom"))
    assert len(caught) == 1
    assert "boom" in str(caught[0].message)
    assert "test_site" in str(caught[0].message)
    assert issubclass(caught[0].category, LoupeTelemetryWarning)


def test_shielded_swallows_exception_and_warns() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", LoupeTelemetryWarning)
        with shielded("flaky_block"):
            raise RuntimeError("oh no")
        # Code after the block continues
    assert len(caught) == 1
    assert "flaky_block" in str(caught[0].message)
    assert "oh no" in str(caught[0].message)


def test_shielded_passes_through_success() -> None:
    counter = {"n": 0}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", LoupeTelemetryWarning)
        with shielded("ok_block"):
            counter["n"] += 1
    assert counter["n"] == 1
    assert caught == []


def test_call_safe_returns_result_on_success() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", LoupeTelemetryWarning)
        result = call_safe(lambda x: x * 2, 21)
    assert result == 42
    assert caught == []


def test_call_safe_returns_none_on_failure() -> None:
    def broken(x: int) -> int:
        raise ZeroDivisionError("nope")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", LoupeTelemetryWarning)
        result = call_safe(broken, 1)
    assert result is None
    assert len(caught) == 1
    assert "nope" in str(caught[0].message)


def test_telemetry_warning_is_filterable() -> None:
    """Users can filter LoupeTelemetryWarning specifically."""
    with warnings.catch_warnings(record=True) as caught:
        # Ignore only Loupe warnings; let others through
        warnings.simplefilter("ignore", LoupeTelemetryWarning)
        emit("site", RuntimeError("x"))
        warnings.warn("not loupe", UserWarning, stacklevel=1)
    # Only the non-loupe one should be recorded
    assert len(caught) == 1
    assert str(caught[0].message) == "not loupe"
