"""Internal telemetry — observe Loupe's own behavior without crashing users.

Rule: Loupe's instrumentation MUST NOT raise into user code. But silent
failure is its own problem — when redaction blows up or an integration
hook misbehaves, the user has no idea why their traces look wrong.

The compromise: catch the exception, emit a Python `warnings.warn` with a
distinctive category (`LoupeTelemetryWarning`), and continue. Users who
want to debug can filter for our category; everyone else sees nothing.

This module is intentionally tiny + dependency-free.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any


class LoupeTelemetryWarning(UserWarning):
    """Emitted when Loupe's own instrumentation catches an exception.

    Filter with:
        import warnings
        from loupe._telemetry import LoupeTelemetryWarning
        warnings.simplefilter("always", LoupeTelemetryWarning)
    """


def emit(where: str, exc: BaseException) -> None:
    """Surface an internal exception via warnings.warn — never raise."""
    warnings.warn(
        f"loupe: caught {type(exc).__name__} in {where}: {exc}",
        LoupeTelemetryWarning,
        stacklevel=3,
    )


@contextmanager
def shielded(where: str) -> Any:
    """Context manager: run a block, surface any error via emit()."""
    try:
        yield
    except Exception as exc:  # noqa: BLE001 — that's the whole point
        emit(where, exc)


def call_safe(fn: Callable[..., Any], *args: Any, where: str = "") -> Any:
    """Invoke fn(*args); on failure, emit + return None."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001
        emit(where or fn.__name__, exc)
        return None
