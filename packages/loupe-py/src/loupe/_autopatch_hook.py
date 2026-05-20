"""Site-time autopatch hook — opt-in via ``LOUPE_AUTOPATCH=1``.

When this module is imported (via the shipped ``loupe-autopatch.pth``
file on every Python startup), it checks one env var:

    LOUPE_AUTOPATCH=1

If set, Loupe activates ``patch_all()`` immediately AND enables
"implicit trace" mode in the universal-httpx interceptor, so every
LLM call captures **without any code changes** in the user's script.

Without the env var, this module is a near-no-op (one ``os.environ``
lookup) so users who installed Loupe but aren't using it right now
don't pay an import-time penalty on every Python startup.

Why a .pth file
---------------
Python's ``site.py`` automatically scans site-packages for ``.pth``
files at interpreter startup and executes any ``import`` statements
they contain. This is the only mechanism that runs *before* the user's
``my_agent.py`` imports its LLM SDK — which is when we need to patch.

Implementation deliberately keeps the imports lazy and the failure
modes silent. A broken Loupe install must never break the user's
Python.
"""

from __future__ import annotations

import os


def _activate() -> None:
    """Run patch_all() + enable implicit trace mode. Best-effort."""
    try:
        from loupe.integrations import patch_all
        patch_all()
    except Exception:  # noqa: BLE001 — autopatch must never break Python startup
        return


# Fast path: env var unset → ~1µs cost, no imports, no side effects.
if os.environ.get("LOUPE_AUTOPATCH") in ("1", "true", "yes", "on"):
    _activate()
