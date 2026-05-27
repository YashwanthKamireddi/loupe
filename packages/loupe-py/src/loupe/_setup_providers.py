"""Single source of truth for providers the ``loupe setup`` wizard offers.

Used by:
  - ``loupe setup`` (interactive picker, key URL, key persistence)
  - ``loupe ask`` / ``loupe chat`` (provider invocation)
  - ``loupe.config`` (env-var override lookup for the configured key)

Mistral, Groq, and DeepSeek all speak the OpenAI chat-completions wire
format, so the actual HTTP call goes through the OpenAI SDK with a
custom ``base_url`` — one invocation path covers all three.

Frontier providers (Gemini, Anthropic, OpenAI) have direct SDK calls
because their auth + body shapes differ enough that the OpenAI-compat
path doesn't fit cleanly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class SetupProvider:
    """One row in the setup wizard's picker, plus everything the rest of
    the CLI needs to do the captured call."""

    label: str            # canonical id: "gemini" / "anthropic" / …
    display: str          # human label: "Google Gemini"
    tagline: str          # one-liner shown in the picker
    env_keys: tuple[str, ...]   # env vars that override the saved key
    key_url: str          # browser destination for create-a-key
    key_prefix: str       # human hint: "AIza…", "sk-ant-…"
    default_model: str
    openai_compat_base_url: str | None = None  # set → routes via OpenAI SDK


# Ordered: frontier first, then inference. The wizard renders this list
# in the same order, so re-ordering here re-orders the picker.
SETUP_PROVIDERS: tuple[SetupProvider, ...] = (
    SetupProvider(
        label="gemini",
        display="Google Gemini",
        tagline="free tier · fastest path to a first trace",
        env_keys=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        key_url="https://aistudio.google.com/apikey",
        key_prefix="AIza…",
        default_model="gemini-2.5-flash",
    ),
    SetupProvider(
        label="anthropic",
        display="Anthropic Claude",
        tagline="best for production agent runs",
        env_keys=("ANTHROPIC_API_KEY",),
        key_url="https://console.anthropic.com/settings/keys",
        key_prefix="sk-ant-…",
        default_model="claude-haiku-4-5-20251001",
    ),
    SetupProvider(
        label="openai",
        display="OpenAI",
        tagline="GPT-4o, o-series · widest framework support",
        env_keys=("OPENAI_API_KEY",),
        key_url="https://platform.openai.com/api-keys",
        key_prefix="sk-…",
        default_model="gpt-4o-mini",
    ),
    SetupProvider(
        label="mistral",
        display="Mistral",
        tagline="European · open + frontier weights",
        env_keys=("MISTRAL_API_KEY",),
        key_url="https://console.mistral.ai/api-keys",
        key_prefix="sk-…",
        default_model="mistral-small-latest",
        openai_compat_base_url="https://api.mistral.ai/v1",
    ),
    SetupProvider(
        label="groq",
        display="Groq",
        tagline="LPU inference · ultra-low latency",
        env_keys=("GROQ_API_KEY",),
        key_url="https://console.groq.com/keys",
        key_prefix="gsk_…",
        default_model="llama-3.3-70b-versatile",
        openai_compat_base_url="https://api.groq.com/openai/v1",
    ),
    SetupProvider(
        label="deepseek",
        display="DeepSeek",
        tagline="open-weights · low cost · long context",
        env_keys=("DEEPSEEK_API_KEY",),
        key_url="https://platform.deepseek.com/api_keys",
        key_prefix="sk-…",
        default_model="deepseek-chat",
        openai_compat_base_url="https://api.deepseek.com/v1",
    ),
)


_BY_LABEL: dict[str, SetupProvider] = {p.label: p for p in SETUP_PROVIDERS}


def get(label: str) -> SetupProvider | None:
    """Look up a provider by its canonical label (case-insensitive)."""
    return _BY_LABEL.get(label.lower().strip())


def labels() -> list[str]:
    """Return the canonical labels in picker order."""
    return [p.label for p in SETUP_PROVIDERS]


def is_supported(label: str) -> bool:
    return label.lower().strip() in _BY_LABEL


def detect_from_env() -> SetupProvider | None:
    """Return the first provider whose env-var key is set, or None.

    Walks ``SETUP_PROVIDERS`` in picker order so a user with multiple
    keys present gets Loupe's preferred default (Gemini → Anthropic →
    OpenAI → others). Used by ``loupe status`` and by the autopatch
    hook to decide whether capture should run without a config file.
    """
    import os
    for p in SETUP_PROVIDERS:
        if any(os.environ.get(k) for k in p.env_keys):
            return p
    return None


# --- Invocation helpers ----------------------------------------------------
#
# Kept in this module so the cli.py call sites become a one-liner. The
# import of the actual SDK (anthropic / openai / google.genai) is lazy
# inside each helper so the CLI doesn't pay startup cost for SDKs you
# haven't configured.


def invoke(
    *,
    provider: str,
    api_key: str,
    model: str,
    history: list[dict[str, str]],
    max_tokens: int = 1024,
) -> str:
    """Run a chat-completion call against the named provider, returning
    the assistant text. ``history`` is ``[{"role": "user", "content": "…"}, …]``.

    Raises ``RuntimeError`` if the provider isn't recognized.
    """
    info = get(provider)
    if info is None:
        raise RuntimeError(f"unknown provider {provider!r}")
    if info.openai_compat_base_url:
        return _invoke_openai_compat(
            base_url=info.openai_compat_base_url,
            api_key=api_key, model=model,
            history=history, max_tokens=max_tokens,
        )
    fn = _DIRECT_INVOKERS.get(info.label)
    if fn is None:
        raise RuntimeError(f"no direct invoker for {provider!r}")
    return fn(api_key, model, history, max_tokens)


def ping(*, provider: str, api_key: str, model: str) -> tuple[bool, str]:
    """Send a tiny request to confirm a key works. Returns ``(ok, message)``.

    Failures are returned as ``(False, "<short reason>")`` instead of
    raising — ``loupe setup`` treats this as a soft "saved but warn" so
    users with iffy networks aren't blocked from saving a key.
    """
    info = get(provider)
    if info is None:
        return False, f"unknown provider {provider!r}"
    try:
        text = invoke(
            provider=provider, api_key=api_key, model=model,
            history=[{"role": "user", "content": "ping"}],
            max_tokens=8,
        )
    except ImportError as exc:
        return False, f"SDK not installed ({exc.name}). pip install {exc.name}"
    except Exception as exc:  # noqa: BLE001 — surface provider error verbatim
        return False, str(exc)[:160]
    return True, f"model {model} responded ({len(text)} chars)"


# --- Direct invokers (frontier providers) ----------------------------------


def _invoke_gemini(
    api_key: str, model: str, history: list[dict[str, str]], max_tokens: int,
) -> str:
    from google import genai
    client = genai.Client(api_key=api_key)
    contents = [
        {
            "role": "user" if m["role"] == "user" else "model",
            "parts": [{"text": m["content"]}],
        }
        for m in history
    ]
    return client.models.generate_content(model=model, contents=contents).text or ""


def _invoke_anthropic(
    api_key: str, model: str, history: list[dict[str, str]], max_tokens: int,
) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[
            {"role": m["role"], "content": m["content"]}  # type: ignore[typeddict-item, misc]
            for m in history
        ],
    )
    return "".join(
        getattr(b, "text", "") for b in (resp.content or [])
        if getattr(b, "type", None) == "text"
    )


def _invoke_openai_native(
    api_key: str, model: str, history: list[dict[str, str]], max_tokens: int,
) -> str:
    return _invoke_openai_compat(
        base_url=None, api_key=api_key, model=model,
        history=history, max_tokens=max_tokens,
    )


def _invoke_openai_compat(
    *,
    base_url: str | None,
    api_key: str,
    model: str,
    history: list[dict[str, str]],
    max_tokens: int,
) -> str:
    """Shared OpenAI-compatible client used by OpenAI, Mistral, Groq, DeepSeek."""
    from openai import OpenAI
    # Use two distinct call shapes instead of **dict unpacking: the
    # OpenAI SDK uses positional-default kwargs whose declared types
    # disagree on a generic str→str mapping, so mypy can't match the
    # overload through a dict unpack.
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model, max_tokens=max_tokens,
        messages=[
            {"role": m["role"], "content": m["content"]}  # type: ignore[misc]
            for m in history
        ],
    )
    return resp.choices[0].message.content or ""


_DIRECT_INVOKERS: dict[str, Callable[[str, str, list[dict[str, str]], int], str]] = {
    "gemini":    _invoke_gemini,
    "anthropic": _invoke_anthropic,
    "openai":    _invoke_openai_native,
}


__all__ = [
    "SETUP_PROVIDERS",
    "SetupProvider",
    "get",
    "invoke",
    "is_supported",
    "labels",
    "ping",
]
