"""Multimodal + tool-call extraction tests.

Real 2026 traffic mixes images, audio, and tool calls into agent
conversations. ``loupe._multimodal`` must:

  1. Strip inline base64 media bytes from captured messages, replacing
     them with a ``{sha256, size_bytes, media_type}`` summary.
  2. Pull tool calls out of three different provider shapes (Anthropic
     ``tool_use`` blocks, OpenAI ``tool_calls`` arrays, Gemini
     ``functionCall`` parts) into one normalized list the dashboard
     can render uniformly.
"""

from __future__ import annotations

import base64
import hashlib

from loupe._multimodal import (
    extract_tool_calls_from_messages,
    extract_tool_calls_from_response,
    scrub_media,
)

# ---------------------------------------------------------------------------
# scrub_media — strip inline binary payloads, keep structure
# ---------------------------------------------------------------------------


def _make_b64(content: bytes = b"fake-png-bytes-deadbeef" * 8) -> str:
    return base64.b64encode(content).decode("ascii")


def test_scrub_media_anthropic_image_block() -> None:
    """An Anthropic image block must lose its ``source.data`` and gain
    a ``_loupe_media`` summary."""
    b64 = _make_b64()
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "what's in this image?"},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64,
                },
            },
        ],
    }
    out = scrub_media([msg])
    assert isinstance(out, list)
    blocks = out[0]["content"]
    # Text block untouched
    assert blocks[0] == {"type": "text", "text": "what's in this image?"}
    # Image block: raw bytes gone, summary present
    img = blocks[1]
    assert img["type"] == "image"
    src = img["source"]
    assert "data" not in src
    assert src["_loupe_media"] is True
    assert src["media_type"] == "image/png"
    assert src["size_bytes"] == len(base64.b64decode(b64))
    assert src["sha256"] == hashlib.sha256(base64.b64decode(b64)).hexdigest()


def test_scrub_media_openai_image_url_data_uri() -> None:
    """OpenAI vision uses ``image_url.url == "data:image/png;base64,..."``."""
    b64 = _make_b64()
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ],
    }
    out = scrub_media([msg])
    img = out[0]["content"][1]
    assert img["type"] == "image_url"
    assert img["image_url"]["_loupe_media"] is True
    assert img["image_url"]["media_type"] == "image/jpeg"


def test_scrub_media_gemini_inline_data() -> None:
    """Gemini ships inline binary under ``inlineData.data``."""
    b64 = _make_b64()
    part = {"inlineData": {"mimeType": "image/png", "data": b64}}
    out = scrub_media(part)
    assert out["inlineData"]["_loupe_media"] is True
    assert out["inlineData"]["media_type"] == "image/png"
    assert "data" not in out["inlineData"]


def test_scrub_media_keeps_urls_and_text_untouched() -> None:
    """URLs (gs://, https://) and plain text must pass through unchanged.

    Same goes for short strings — anything ≤64 chars isn't worth the
    overhead of hashing + summarizing.
    """
    payload = [
        {"type": "text", "text": "hello"},
        {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
        {"fileData": {"mimeType": "image/png", "fileUri": "gs://bucket/cat.png"}},
        # Short b64 - below the 64-char threshold
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
        },
    ]
    out = scrub_media(payload)
    # text untouched
    assert out[0] == payload[0]
    # http(s) URL untouched (no base64 marker)
    assert out[1] == payload[1]
    # gs:// path untouched
    assert out[2] == payload[2]
    # short b64 passes through (not worth hashing)
    assert out[3] == payload[3]


def test_scrub_media_recurses_into_nested_structures() -> None:
    """An image buried two levels deep still gets scrubbed."""
    b64 = _make_b64()
    nested = {
        "outer": {
            "inner": [
                {"type": "image", "source": {"type": "base64",
                                              "media_type": "image/png",
                                              "data": b64}},
            ],
        },
    }
    out = scrub_media(nested)
    block = out["outer"]["inner"][0]
    assert block["source"]["_loupe_media"] is True


def test_scrub_media_handles_invalid_base64_gracefully() -> None:
    """Corrupt base64 must not crash — falls back to hashing the raw
    string and recording its length."""
    bad = "@@@" * 50   # not valid base64, but long enough to trigger
    msg = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": bad},
    }
    out = scrub_media(msg)
    src = out["source"]
    assert src["_loupe_media"] is True
    assert src["size_bytes"] == len(bad)


# ---------------------------------------------------------------------------
# Tool-call extraction — three provider shapes, one normalized output
# ---------------------------------------------------------------------------


def test_extract_tool_calls_anthropic_response() -> None:
    """Anthropic returns tool_use as a content block."""
    body = {
        "content": [
            {"type": "text", "text": "let me check the weather"},
            {
                "type": "tool_use",
                "id": "toolu_abc",
                "name": "get_weather",
                "input": {"city": "SF", "unit": "C"},
            },
        ],
    }
    out = extract_tool_calls_from_response("anthropic", body)
    assert out == [{
        "name": "get_weather",
        "arguments": {"city": "SF", "unit": "C"},
        "id": "toolu_abc",
    }]


def test_extract_tool_calls_openai_response() -> None:
    """OpenAI puts them under choices[0].message.tool_calls with JSON-string args."""
    body = {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "search",
                        "arguments": '{"q":"loupe","limit":5}',
                    },
                }],
            },
        }],
    }
    out = extract_tool_calls_from_response("openai", body)
    assert len(out) == 1
    assert out[0]["name"] == "search"
    assert out[0]["arguments"] == {"q": "loupe", "limit": 5}
    assert out[0]["id"] == "call_1"


def test_extract_tool_calls_openai_compatible_provider() -> None:
    """Mistral/Groq/etc. all speak OpenAI spec — same shape, same extractor."""
    body = {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "id": "abc",
                    "function": {"name": "ping", "arguments": "{}"},
                }],
            },
        }],
    }
    out = extract_tool_calls_from_response("openai-compatible:api.mistral.ai", body)
    assert len(out) == 1
    assert out[0]["name"] == "ping"


def test_extract_tool_calls_gemini_response() -> None:
    """Gemini ships them as functionCall parts."""
    body = {
        "candidates": [{
            "content": {
                "parts": [
                    {"text": "checking..."},
                    {"functionCall": {"name": "weather", "args": {"city": "SF"}}},
                ],
            },
        }],
    }
    out = extract_tool_calls_from_response("gemini", body)
    assert out == [{"name": "weather", "arguments": {"city": "SF"}}]


def test_extract_tool_calls_no_tool_use_returns_empty() -> None:
    """A normal text-only response yields no tool calls."""
    body = {"content": [{"type": "text", "text": "just talking"}]}
    assert extract_tool_calls_from_response("anthropic", body) == []


def test_extract_tool_calls_from_messages_anthropic() -> None:
    """Prior assistant turn that contained a tool_use block must be
    surfaced in inputs.tool_calls."""
    messages = [
        {"role": "user", "content": "what's the weather?"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_x", "name": "weather",
             "input": {"city": "NYC"}},
        ]},
    ]
    out = extract_tool_calls_from_messages(messages)
    assert out == [{"name": "weather", "arguments": {"city": "NYC"}, "id": "toolu_x"}]


def test_extract_tool_calls_from_messages_openai() -> None:
    """Same idea, OpenAI-shape conversation history."""
    messages = [
        {"role": "user", "content": "list users"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "c1", "function": {"name": "list", "arguments": '{"limit":5}'}},
            ],
        },
    ]
    out = extract_tool_calls_from_messages(messages)
    assert out == [{"name": "list", "arguments": {"limit": 5}, "id": "c1"}]


# ---------------------------------------------------------------------------
# Negative tests — never crash on weird inputs
# ---------------------------------------------------------------------------


def test_scrub_media_handles_non_dict_non_list_inputs() -> None:
    """``scrub_media`` is the entry point of an untrusted pipeline. It
    must accept scalars / None / arbitrary objects without raising."""
    assert scrub_media(None) is None
    assert scrub_media("hello") == "hello"
    assert scrub_media(42) == 42
    assert scrub_media(True) is True


def test_extract_tool_calls_handles_malformed_payloads() -> None:
    """Garbage in → empty list out. Never raises."""
    assert extract_tool_calls_from_response("anthropic", {}) == []
    assert extract_tool_calls_from_response("openai", {"choices": []}) == []
    assert extract_tool_calls_from_response("gemini", {"candidates": [{}]}) == []
    assert extract_tool_calls_from_response("anthropic", {"content": [None, 1, "a"]}) == []
    assert extract_tool_calls_from_messages([None, "x", 1]) == []
