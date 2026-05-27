"""Site-time autopatch hook — on by default whenever a provider key
is detectable, no manual setup required.

This module runs at every Python interpreter startup via the shipped
``loupe-autopatch.pth`` file. Whether it activates capture depends on:

    1. ``LOUPE_AUTOPATCH=0`` (off, false, no, "") → never activate
       (explicit opt-out — always honored, even with a config + env key)
    2. ``LOUPE_AUTOPATCH=1`` (or true / yes / on) → activate now
       (explicit opt-in)
    3. Env var unset:
         • ``~/.loupe/config.toml`` exists → activate
           (the user ran ``loupe setup``; we honour their intent)
         • any provider env var detected (``OPENAI_API_KEY``,
           ``ANTHROPIC_API_KEY``, ``GEMINI_API_KEY``,
           ``GOOGLE_API_KEY``, ``MISTRAL_API_KEY``,
           ``GROQ_API_KEY``, ``DEEPSEEK_API_KEY``) → activate
           (zero-friction: a key is already in the environment, so the
           user clearly intends to call an LLM — capture the calls)
         • neither config nor any key   → do nothing
           (probably a transitive install; never surprise people)

Hot path when off: a few ``os.environ.get`` checks + one ``Path.exists``
= still <10µs at Python startup, no imports, no side effects on the
user's program.

Why a .pth file
---------------
Python's ``site.py`` automatically scans site-packages for ``.pth``
files at interpreter startup and executes any ``import`` statements
they contain. This is the only mechanism that runs *before* the user's
``my_agent.py`` imports its LLM SDK — which is when we need to patch.

A broken Loupe install must never break the user's Python — every
exception below is swallowed silently.
"""

from __future__ import annotations

import os

# Provider env vars Loupe knows how to capture. Kept in sync with
# loupe._setup_providers.SETUP_PROVIDERS; duplicated here so the .pth
# hot path doesn't have to import the heavier provider registry.
_PROVIDER_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "DEEPSEEK_API_KEY",
)


def _any_provider_env_var_set() -> bool:
    """Return True if at least one known provider key is in the environment."""
    return any(os.environ.get(name) for name in _PROVIDER_ENV_VARS)


def _should_activate() -> bool:
    """Resolve the autopatch decision per the documented order above.

    Inlined here (not imported from loupe.integrations.httpx) so the
    .pth hot path never imports the integrations subpackage when the
    answer is "off".
    """
    raw = os.environ.get("LOUPE_AUTOPATCH")
    if raw is not None:
        norm = raw.strip().lower()
        if norm in ("1", "true", "yes", "on"):
            return True
        if norm in ("0", "false", "no", "off", ""):
            return False
        return False
    # Env var unset → activate if EITHER (a) the user ran `loupe setup`
    # OR (b) a recognized provider key is present in the environment.
    try:
        from pathlib import Path
        home = os.environ.get("LOUPE_HOME")
        root = Path(home) if home else Path.home() / ".loupe"
        if (root / "config.toml").exists():
            return True
    except Exception:  # noqa: BLE001 — never break Python startup
        # Fall through to env-var detection
        pass
    return _any_provider_env_var_set()


def _activate() -> None:
    """Run patch_all() + enable implicit trace mode. Best-effort."""
    try:
        from loupe.integrations import patch_all
        patch_all()
    except Exception:  # noqa: BLE001 — autopatch must never break Python startup
        return


if _should_activate():
    _activate()
