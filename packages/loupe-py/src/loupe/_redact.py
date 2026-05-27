"""Secret redaction for captured request data.

The captures we ship to ~/.loupe/traces/ are local-only by default, but the
moment a user shares one (loupe report, loupe export, screenshot, paste into
a Slack thread) any embedded API keys leak. So every integration that builds
an `inputs` dict from a wire payload should run it through `redact()` first.

We redact aggressively: anything that looks like a key/token/secret name,
plus anything inside a value matching common credential patterns
(Bearer xxx, sk-…, gho_…, eyJ…).

Stability rules:
- Never raise on malformed input — the redactor must never crash a trace.
- Always return the same type as the input (dict → dict, list → list, str → str).
- Idempotent: redact(redact(x)) == redact(x).
"""

from __future__ import annotations

import re
from typing import Any

# Field names that ALWAYS get scrubbed, regardless of value.
# Match on substring — any key whose name *contains* one of these words is a
# secret. False positives are fine; data loss isn't.
_SECRET_NAME_PATTERNS = re.compile(
    r"(authorization|api[-_]?key|apikey|secret|token|password|bearer|"
    r"private[-_]?key|access[-_]?key|x[-_]?auth)",
    re.IGNORECASE,
)

# Substrings that, when seen inside a string value, indicate a credential.
# Conservative — false positives just mean we redact one extra value.
_SECRET_VALUE_PATTERNS = [
    re.compile(r"\bBearer\s+[A-Za-z0-9_\-./+=]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),        # OpenAI-style
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),    # Anthropic
    re.compile(r"\bsk-or-[A-Za-z0-9_\-]{16,}"),     # OpenRouter
    re.compile(r"\bgsk_[A-Za-z0-9_\-]{20,}"),       # Groq
    re.compile(r"\bgho_[A-Za-z0-9_]{20,}"),         # GitHub OAuth
    re.compile(r"\bghp_[A-Za-z0-9_]{20,}"),         # GitHub PAT
    re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}"),       # Google API key
    # JWTs: three base64url segments. Be tolerant of segment length;
    # the structure (eyJ…+two dot-separated parts) is the giveaway.
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
]

_REDACTED = "[redacted]"
# Cap walk depth to avoid runaway recursion on cyclic / very-deep objects.
_MAX_DEPTH = 8


# J2 — User-defined regex patterns from ``~/.loupe/config.toml``.
# Loaded lazily on the first redact() call so importing this module stays
# free of side effects + dependency-cheap.
_CUSTOM_PATTERNS_CACHE: list[re.Pattern[str]] | None = None
_CUSTOM_PATTERNS_LOADED_FOR_CONFIG: str | None = None


def _load_custom_patterns() -> list[re.Pattern[str]]:
    """Return the cached compiled list of user-defined redaction patterns.

    Re-reads the config file when its path or modification token changes
    (cheap: compares the path string only). Compile failures are logged
    once via warnings and the bad pattern is skipped — never raise into
    the capture path.
    """
    global _CUSTOM_PATTERNS_CACHE, _CUSTOM_PATTERNS_LOADED_FOR_CONFIG
    try:
        from loupe.config import Config, config_path
    except Exception:  # noqa: BLE001 — config import must never break redact
        return []
    cfg_token = str(config_path()) if hasattr(Config, "load") else ""
    if (
        _CUSTOM_PATTERNS_CACHE is not None
        and cfg_token == _CUSTOM_PATTERNS_LOADED_FOR_CONFIG
    ):
        return _CUSTOM_PATTERNS_CACHE
    compiled: list[re.Pattern[str]] = []
    try:
        cfg = Config.load()
        for raw in cfg.redact_patterns:
            try:
                compiled.append(re.compile(raw))
            except re.error:
                # Bad regex — skip; the doctor command (`loupe doctor`)
                # can validate user config separately.
                continue
    except Exception:  # noqa: BLE001
        compiled = []
    _CUSTOM_PATTERNS_CACHE = compiled
    _CUSTOM_PATTERNS_LOADED_FOR_CONFIG = cfg_token
    return compiled


def _reset_custom_pattern_cache() -> None:
    """For tests: force the next ``redact()`` call to re-read config."""
    global _CUSTOM_PATTERNS_CACHE, _CUSTOM_PATTERNS_LOADED_FOR_CONFIG
    _CUSTOM_PATTERNS_CACHE = None
    _CUSTOM_PATTERNS_LOADED_FOR_CONFIG = None


def redact(value: Any) -> Any:
    """Return a deeply-redacted copy of `value`.

    Strings, ints, floats, bools, None pass through (strings get pattern scan).
    Dicts have known-secret keys replaced with `[redacted]`.
    Lists/tuples are walked element-wise.
    Unknown types are returned as-is.
    """
    return _redact(value, depth=0)


def _redact(value: Any, *, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        return {
            k: (
                _REDACTED
                if isinstance(k, str) and _SECRET_NAME_PATTERNS.search(k)
                else _redact(v, depth=depth + 1)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, depth=depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item, depth=depth + 1) for item in value)
    return value


def _redact_string(s: str) -> str:
    """Replace credential-shaped substrings with [redacted].

    Applies the built-in patterns first, then any user-configured
    regexes from ``~/.loupe/config.toml`` ``[redact] patterns``.
    """
    redacted = s
    for pattern in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(_REDACTED, redacted)
    for pattern in _load_custom_patterns():
        redacted = pattern.sub(_REDACTED, redacted)
    return redacted
