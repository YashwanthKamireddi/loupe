"""Multimodal-input + tool-call hygiene for captured LLM traces.

Two real-world 2026 problems this module solves:

1. **Inline images / audio explode trace size.**
   When an agent sends a base64-encoded image to Claude or GPT-4o, the
   payload can be hundreds of kilobytes of binary data. Capturing it
   verbatim into ``~/.loupe/traces/<id>.jsonl`` makes:
     - the dashboard render slow (it re-parses every step on click)
     - ``loupe export`` produce uselessly large OTLP documents
     - the JSONL file unreadable in a text editor

   Solution: detect inline media blocks, replace the binary payload
   with ``{sha256, size_bytes, media_type}`` metadata. The structure
   stays intact (so the dashboard can render an "[image: 2 KB png]"
   placeholder), but the raw bytes never hit disk.

2. **Tool calls weren't first-class.**
   Anthropic ships tool calls as ``{"type": "tool_use", ...}`` content
   blocks. OpenAI ships them as ``choices[0].message.tool_calls``.
   Gemini ships them as ``candidates[0].content.parts[*].functionCall``.

   Without targeted extraction the dashboard saw them as opaque dicts.
   This module surfaces ``inputs.tool_calls`` and ``outputs.tool_calls``
   as a normalized list of ``{name, arguments}`` so the UI can render
   them as their own step-kind row.

Both pieces are pure functions — input dict, output dict — so they can
be unit-tested without spinning up any of the integration paths.
"""

from __future__ import annotations

import hashlib
import json as _json
from typing import Any

# A block is "inline media" if it carries actual bytes (vs a URL). The
# shapes we cover, normalized across providers:
#
# Anthropic image: {"type": "image", "source": {"type": "base64",
#                   "media_type": "image/png", "data": "<b64...>"}}
# Anthropic doc:   {"type": "document", "source": {"type": "base64",
#                   "media_type": "application/pdf", "data": "<b64...>"}}
# OpenAI vision:   {"type": "image_url", "image_url": {"url": "data:image/png;base64,<b64>"}}
# Gemini inline:   {"inlineData": {"mimeType": "image/png", "data": "<b64...>"}}
# Gemini file:     {"fileData": {"mimeType": "image/png", "fileUri": "gs://..."}}
#                                                          ← already small, pass through

_BASE64_MARKER = "base64,"


def scrub_media(value: Any) -> Any:
    """Walk a nested structure and replace inline base64 media payloads
    with metadata summaries. Pure, immutable — returns a new object.

    Recurses through lists and dicts. Leaves unrelated values alone.
    """
    if isinstance(value, list):
        return [scrub_media(v) for v in value]
    if isinstance(value, dict):
        # Anthropic image / document
        if value.get("type") in ("image", "document"):
            src = value.get("source")
            if isinstance(src, dict) and src.get("type") == "base64":
                data = src.get("data")
                if isinstance(data, str) and len(data) > 64:
                    return {
                        **{k: v for k, v in value.items() if k != "source"},
                        "source": _hash_summary(
                            data,
                            media_type=src.get("media_type") or "application/octet-stream",
                        ),
                    }
        # OpenAI vision: image_url.url == "data:image/png;base64,..."
        if value.get("type") == "image_url":
            img = value.get("image_url")
            if isinstance(img, dict):
                url = img.get("url")
                if isinstance(url, str) and _BASE64_MARKER in url and len(url) > 64:
                    head, _, payload = url.partition(_BASE64_MARKER)
                    media_type = head.removeprefix("data:").rstrip(";")
                    return {
                        **{k: v for k, v in value.items() if k != "image_url"},
                        "image_url": _hash_summary(payload, media_type=media_type),
                    }
        # Gemini inlineData
        if "inlineData" in value and isinstance(value["inlineData"], dict):
            inline = value["inlineData"]
            data = inline.get("data")
            if isinstance(data, str) and len(data) > 64:
                return {
                    **{k: v for k, v in value.items() if k != "inlineData"},
                    "inlineData": _hash_summary(
                        data,
                        media_type=inline.get("mimeType") or "application/octet-stream",
                    ),
                }
        return {k: scrub_media(v) for k, v in value.items()}
    return value


def _hash_summary(b64_data: str, *, media_type: str) -> dict[str, Any]:
    """Replace a base64 string with ``{sha256, size_bytes, media_type}``.

    Hash is the SHA-256 of the *raw* (decoded) bytes when possible —
    that's the identifier two captures of the same image will share.
    Falls back to hashing the base64 string itself when decoding fails.
    """
    import base64
    try:
        raw = base64.b64decode(b64_data, validate=True)
        size = len(raw)
        digest = hashlib.sha256(raw).hexdigest()
    except Exception:
        size = len(b64_data)
        digest = hashlib.sha256(b64_data.encode("utf-8", errors="replace")).hexdigest()
    return {
        "_loupe_media": True,
        "media_type": media_type,
        "size_bytes": size,
        "sha256": digest,
    }


# ---------------------------------------------------------------------------
# Tool-call extraction
# ---------------------------------------------------------------------------


def extract_tool_calls_from_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """Extract tool-use blocks from an inbound messages array.

    Returns a normalized list of ``{name, arguments, id?}`` dicts.
    Used to surface tool calls from the *input* (e.g. assistant's prior
    turn) so the dashboard can show conversation history that already
    contains tool invocations.
    """
    out: list[dict[str, Any]] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                # Anthropic
                if block.get("type") == "tool_use":
                    out.append({
                        "name": block.get("name"),
                        "arguments": block.get("input") or {},
                        "id": block.get("id"),
                    })
        # OpenAI assistant turns
        for tc in m.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            arg_raw = fn.get("arguments")
            try:
                arg_parsed = (
                    _json.loads(arg_raw)
                    if isinstance(arg_raw, str) else arg_raw
                )
            except _json.JSONDecodeError:
                arg_parsed = arg_raw
            out.append({
                "name": fn.get("name"),
                "arguments": arg_parsed or {},
                "id": tc.get("id"),
            })
    return out


def extract_tool_calls_from_response(provider: str, body: dict) -> list[dict[str, Any]]:
    """Extract tool calls the model wants to invoke from the response.

    Returns a normalized list. Three provider shapes handled:

    - **Anthropic**: ``body.content[*]`` where ``type == "tool_use"``
    - **OpenAI**:    ``body.choices[0].message.tool_calls[*]``
    - **Gemini**:    ``body.candidates[0].content.parts[*].functionCall``
    """
    out: list[dict[str, Any]] = []

    if provider == "anthropic":
        for block in body.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                out.append({
                    "name": block.get("name"),
                    "arguments": block.get("input") or {},
                    "id": block.get("id"),
                })
    elif provider == "openai" or provider.startswith("openai-compatible:"):
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") or {}
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                arg_raw = fn.get("arguments")
                try:
                    arg_parsed = (
                        _json.loads(arg_raw)
                        if isinstance(arg_raw, str) else arg_raw
                    )
                except _json.JSONDecodeError:
                    arg_parsed = arg_raw
                out.append({
                    "name": fn.get("name"),
                    "arguments": arg_parsed or {},
                    "id": tc.get("id"),
                })
    elif provider == "gemini":
        for cand in body.get("candidates") or []:
            cnt = cand.get("content") or {}
            for part in cnt.get("parts") or []:
                if isinstance(part, dict) and "functionCall" in part:
                    fc = part["functionCall"] or {}
                    out.append({
                        "name": fc.get("name"),
                        "arguments": fc.get("args") or {},
                    })

    return out


__all__ = [
    "extract_tool_calls_from_messages",
    "extract_tool_calls_from_response",
    "scrub_media",
]
