"""Shared autopatch primitives used by both the universal httpx interceptor
and the direct-SDK integrations (openai, anthropic, ...).

Why this is shared
------------------
Loupe's "zero-code capture" pitch is that any LLM call from any Python
script with ``LOUPE_AUTOPATCH=1`` lands in a trace, *without* needing a
``@trace`` decorator around the script.

The universal httpx layer implemented this via :func:`implicit_trace_context`
— but the direct integrations (which take over when a known SDK like
``openai`` is installed) used to silently no-op when no parent ``@trace``
existed. That made autopatch effectively broken for the most common case:
``pip install loupe-ai openai`` → no captures.

This module centralizes the autopatch gate + the implicit-trace context
so every integration uses the same logic.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

_TRUTHY = ("1", "true", "yes", "on")


def autopatch_enabled() -> bool:
    """Whether autopatch should auto-create an implicit one-call trace.

    Resolution order (first match wins):

      1. ``LOUPE_AUTOPATCH=0`` / ``false`` / ``no`` / ``off`` / empty → OFF
         (explicit opt-out; always honored).
      2. ``LOUPE_AUTOPATCH=1`` / ``true`` / ``yes`` / ``on``  → ON
         (explicit opt-in; useful before ``loupe setup`` has run).
      3. Env var unset:
           - ``~/.loupe/config.toml`` exists → ON (user ran setup; they
             clearly want capture).
           - Otherwise → OFF (probably a transitive install; don't surprise
             unrelated codebases).
    """
    raw = os.environ.get("LOUPE_AUTOPATCH")
    if raw is not None:
        norm = raw.strip().lower()
        if norm in _TRUTHY:
            return True
        if norm in ("0", "false", "no", "off", ""):
            return False
        return False  # unknown value → safer default
    try:
        from loupe.config import config_path
        return config_path().exists()
    except Exception:  # noqa: BLE001 — config import can't crash autopatch
        return False


def _script_name() -> str:
    """Name implicit traces after the calling script when possible."""
    try:
        if sys.argv and sys.argv[0] and sys.argv[0] not in ("-c", "-"):
            stem = Path(sys.argv[0]).stem
            if stem and stem not in ("python", "python3"):
                return stem
    except Exception:  # noqa: BLE001 — naming is best-effort
        pass
    return "auto"


@contextlib.contextmanager
def implicit_trace_context() -> Iterator[None]:
    """Create a one-call anonymous trace on the current task.

    Yields nothing; the caller does its normal capture work inside the
    block. On exit, the trace is finalized and written to the default
    store. Designed to be cheap — runs only when a real LLM call is
    about to happen and no parent trace exists.
    """
    from loupe.store import default_store
    from loupe.trace import _begin_trace, _current_trace, _finish_trace

    t = _begin_trace(_script_name(), "autopatch")
    token = _current_trace.set(t)
    try:
        yield
    except BaseException as exc:
        t.metadata["failed"] = True
        t.metadata["error"] = repr(exc)
        raise
    finally:
        _finish_trace(t, default_store())
        _current_trace.reset(token)


@contextlib.contextmanager
def ensure_implicit_trace_if_autopatch() -> Iterator[None]:
    """If autopatch is enabled and no parent trace exists, open one for the
    duration of this block. Otherwise pass through unchanged.

    Direct integrations wrap the SDK call site with this, so a bare
    ``pip install loupe-ai openai`` user gets capture without needing
    a ``@trace`` decorator.
    """
    from loupe.trace import current_trace

    if current_trace() is None and autopatch_enabled():
        with implicit_trace_context():
            yield
    else:
        yield


def safe_emit(emit_fn: Any, *args: Any, **kwargs: Any) -> None:
    """Run ``emit_fn(*args, **kwargs)`` with errors swallowed.

    Direct integrations call this from a ``finally`` block — a crash here
    must never bubble out of the user's LLM call. Best-effort only.
    """
    with contextlib.suppress(Exception):
        emit_fn(*args, **kwargs)
