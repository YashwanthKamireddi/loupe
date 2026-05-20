"""User configuration — a single TOML file at ``~/.loupe/config.toml``.

This is the durable, source-of-truth replacement for the scattered
env-var-driven configuration that historically spanned ``LOUPE_HOME``,
``GEMINI_API_KEY``, ``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``,
``LOUPE_DISABLE_INDEX``, ``LOUPE_DISABLE_NEURONPEDIA``, and more.

Schema (every section is optional; first-run will create a minimal file)::

    [default]
    model = "gemini-2.5-flash"
    provider = "gemini"

    [providers.gemini]
    api_key = "AIza..."

    [providers.anthropic]
    api_key = "sk-ant-..."

    [providers.openai]
    api_key = "sk-..."

    [attribution]
    backend = "mock"          # or "sae"

    [index]
    disabled = false

    [updates]
    check_on_startup = true

Compatibility:
    - Env vars (``GEMINI_API_KEY``, etc.) still work and win over the
      config file. This module exposes :func:`api_key_for(provider)`
      that honours that precedence so call sites can be one-liners.
    - ``LOUPE_HOME`` env var still controls where the config lives.

This module is **pure** — no side effects on import, no global state
beyond the config-path resolution.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loupe.store import _default_dir

# --- Path resolution -------------------------------------------------------


def config_path() -> Path:
    """Absolute path to the active config file (does not require it to exist)."""
    return _default_dir() / "config.toml"


# --- Defaults (used when keys are missing) ---------------------------------


_DEFAULTS: dict[str, Any] = {
    "default": {
        "model": "gemini-2.5-flash",
        "provider": "gemini",
    },
    "providers": {},
    "attribution": {
        "backend": "mock",
    },
    "index": {
        "disabled": False,
    },
    "updates": {
        "check_on_startup": True,
    },
}


# --- Public API ------------------------------------------------------------


@dataclass
class ProviderConfig:
    """One provider entry in ``[providers.<name>]``."""

    name: str
    api_key: str | None = None
    base_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_configured(self) -> bool:
        return bool(self.api_key)


@dataclass
class Config:
    """In-memory view of the user config file plus env-var overrides.

    Instances are immutable from the caller's perspective; mutate by
    constructing a new :class:`Config` and calling :meth:`save`.
    """

    default_provider: str = "gemini"
    default_model: str = "gemini-2.5-flash"
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    attribution_backend: str = "mock"
    index_disabled: bool = False
    check_for_updates: bool = True
    _path: Path | None = None

    # --- factory --------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Read the config file (if any), layering env-var overrides on top.

        Missing file → returns a fresh default config. The caller decides
        whether to ``.save()`` it. Malformed TOML → returns defaults with
        the corrupted path preserved on ``_path`` so ``loupe doctor``
        can surface the parse error.
        """
        p = path or config_path()
        data: dict[str, Any] = {}
        if p.exists():
            try:
                data = tomllib.loads(p.read_text(encoding="utf-8"))
            except (tomllib.TOMLDecodeError, OSError):
                # We intentionally don't raise here — the caller can ask
                # for a doctor-style health check separately. A corrupt
                # config must not crash `loupe` startup.
                data = {}

        merged = _deep_merge(_DEFAULTS, data)

        providers: dict[str, ProviderConfig] = {}
        for name, body in (merged.get("providers") or {}).items():
            if not isinstance(body, dict):
                continue
            providers[name] = ProviderConfig(
                name=name,
                api_key=_str_or_none(body.get("api_key")),
                base_url=_str_or_none(body.get("base_url")),
                metadata={
                    k: v for k, v in body.items()
                    if k not in {"api_key", "base_url"}
                },
            )

        return cls(
            default_provider=str(merged["default"]["provider"]),
            default_model=str(merged["default"]["model"]),
            providers=providers,
            attribution_backend=str(merged["attribution"]["backend"]),
            index_disabled=bool(merged["index"]["disabled"]),
            check_for_updates=bool(merged["updates"]["check_on_startup"]),
            _path=p,
        )

    # --- queries --------------------------------------------------------

    def api_key_for(self, provider: str) -> str | None:
        """Return the API key for ``provider``, honouring env-var overrides.

        Lookup order:

        1. Provider-specific env var (``GEMINI_API_KEY`` /
           ``GOOGLE_API_KEY`` for gemini, ``ANTHROPIC_API_KEY`` for
           anthropic, ``OPENAI_API_KEY`` for openai)
        2. Config file ``[providers.<provider>].api_key``
        """
        for env_name in _env_keys_for(provider):
            v = os.environ.get(env_name)
            if v:
                return v
        cfg = self.providers.get(provider)
        if cfg is not None and cfg.api_key:
            return cfg.api_key
        return None

    def configured_providers(self) -> list[str]:
        """Names of providers that have a usable API key (env or config)."""
        names: list[str] = []
        for name in sorted({"gemini", "anthropic", "openai", *self.providers.keys()}):
            if self.api_key_for(name):
                names.append(name)
        return names

    # --- mutation -------------------------------------------------------

    def set_provider_key(self, provider: str, api_key: str) -> Config:
        """Return a new Config with ``provider``'s key set."""
        new_providers = dict(self.providers)
        existing = new_providers.get(provider) or ProviderConfig(name=provider)
        new_providers[provider] = ProviderConfig(
            name=provider,
            api_key=api_key,
            base_url=existing.base_url,
            metadata=dict(existing.metadata),
        )
        return Config(
            default_provider=self.default_provider,
            default_model=self.default_model,
            providers=new_providers,
            attribution_backend=self.attribution_backend,
            index_disabled=self.index_disabled,
            check_for_updates=self.check_for_updates,
            _path=self._path,
        )

    def with_default(self, *, provider: str | None = None, model: str | None = None) -> Config:
        """Return a new Config with the default provider/model changed."""
        return Config(
            default_provider=provider or self.default_provider,
            default_model=model or self.default_model,
            providers=dict(self.providers),
            attribution_backend=self.attribution_backend,
            index_disabled=self.index_disabled,
            check_for_updates=self.check_for_updates,
            _path=self._path,
        )

    # --- persistence ----------------------------------------------------

    def save(self) -> Path:
        """Write the config to disk in TOML format. Atomic via tmp+rename.

        Returns the path actually written, for the caller to log.
        """
        p = self._path or config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        body = self._to_toml()
        tmp = p.with_suffix(".toml.tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, p)
        return p

    def _to_toml(self) -> str:
        """Render this Config as a stable TOML string. Hand-written rather
        than via ``tomli_w`` so we get the exact section ordering and
        explanatory comments a real user expects to read."""
        out: list[str] = []
        out.append("# Loupe config — edit by hand or with `loupe setup`.")
        out.append("# Env vars (GEMINI_API_KEY etc.) still work and take precedence.")
        out.append("")
        out.append("[default]")
        out.append(f'provider = "{_escape(self.default_provider)}"')
        out.append(f'model    = "{_escape(self.default_model)}"')
        out.append("")
        out.append("[attribution]")
        out.append(f'backend = "{_escape(self.attribution_backend)}"')
        out.append("")
        out.append("[index]")
        out.append(f"disabled = {str(self.index_disabled).lower()}")
        out.append("")
        out.append("[updates]")
        out.append(f"check_on_startup = {str(self.check_for_updates).lower()}")
        out.append("")
        for name in sorted(self.providers):
            p = self.providers[name]
            if not (p.api_key or p.base_url or p.metadata):
                continue
            out.append(f"[providers.{name}]")
            if p.api_key:
                out.append(f'api_key  = "{_escape(p.api_key)}"')
            if p.base_url:
                out.append(f'base_url = "{_escape(p.base_url)}"')
            for k, v in p.metadata.items():
                out.append(f'{k} = "{_escape(str(v))}"')
            out.append("")
        return "\n".join(out).rstrip() + "\n"


# --- Helpers ---------------------------------------------------------------


def _env_keys_for(provider: str) -> tuple[str, ...]:
    """Canonical env-var names accepted for each provider."""
    table = {
        "gemini":    ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "google":    ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "anthropic": ("ANTHROPIC_API_KEY",),
        "openai":    ("OPENAI_API_KEY",),
    }
    return table.get(provider.lower(), ())


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value
    return None


def _escape(s: str) -> str:
    """Escape a string for TOML basic-string syntax (no triple quotes)."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively overlay ``overlay`` onto ``base``. Returns a new dict.

    Used to merge user-provided TOML on top of :data:`_DEFAULTS`.
    """
    result: dict[str, Any] = {}
    keys = set(base.keys()) | set(overlay.keys())
    for k in keys:
        b_dict = isinstance(base.get(k), dict)
        o_dict = isinstance(overlay.get(k), dict)
        if k in base and k in overlay and b_dict and o_dict:
            result[k] = _deep_merge(base[k], overlay[k])
        elif k in overlay:
            result[k] = overlay[k]
        else:
            result[k] = base[k]
    return result


__all__ = [
    "Config",
    "ProviderConfig",
    "config_path",
]
