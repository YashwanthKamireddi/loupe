"""Secret redaction tests — every captured payload runs through this first."""

from __future__ import annotations

from loupe._redact import redact


def test_redact_passes_primitives_unchanged() -> None:
    assert redact(None) is None
    assert redact(True) is True
    assert redact(42) == 42
    assert redact(3.14) == 3.14
    assert redact("hello world") == "hello world"


def test_redact_replaces_known_secret_keys() -> None:
    inp = {
        "model": "claude",
        "api_key": "sk-ant-abcdefg12345",
        "messages": [{"role": "user", "content": "hi"}],
    }
    out = redact(inp)
    assert out["model"] == "claude"
    assert out["api_key"] == "[redacted]"
    assert out["messages"] == [{"role": "user", "content": "hi"}]


def test_redact_handles_various_key_styles() -> None:
    inp = {
        "Authorization": "Bearer sk-1234567890abcdefgh",
        "X-API-Key": "abc",
        "x_auth_token": "xyz",
        "access-key": "k",
        "Password": "p",
        "secret_key": "s",
        "user_authorization_header": "Bearer wat",
    }
    out = redact(inp)
    for key in inp:
        assert out[key] == "[redacted]", f"{key} was not redacted"


def test_redact_redacts_bearer_in_string_values() -> None:
    inp = "Header: Bearer sk-1234567890abcdefghij"
    assert redact(inp) == "Header: [redacted]"


def test_redact_redacts_known_token_shapes() -> None:
    samples = [
        "use this: sk-ant-abcdefghij1234567890abcdef and we're good",
        "OPENAI=sk-AbCdEfGhIjKlMnOpQrStUv",
        "OPENROUTER=sk-or-AbCdEfGhIjKlMnOpQ",
        "GROQ=gsk_AbCdEfGhIjKlMnOpQrSt12",
        "token=gho_AbCdEfGhIjKlMnOpQrStUvWxYz12",
        "GH PAT: ghp_AbCdEfGhIjKlMnOpQrStUv1234",
        "google: AIzaSyAbCdEfGhIjKlMnOpQrStUv",
        # JWT (each segment ≥ 8 chars; structure is the tell)
        (
            "auth: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkw"
            ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        ),
    ]
    for s in samples:
        out = redact(s)
        assert "[redacted]" in out, f"failed to redact in: {s}"


def test_redact_walks_nested_structures() -> None:
    inp = {
        "level1": {
            "level2": [
                {"api_key": "should_disappear", "value": 1},
                "Bearer sk-ant-zzzzzzzzzzzzzzz",
            ],
        }
    }
    out = redact(inp)
    assert out["level1"]["level2"][0]["api_key"] == "[redacted]"
    assert out["level1"]["level2"][0]["value"] == 1
    assert out["level1"]["level2"][1] == "[redacted]"


def test_redact_is_idempotent() -> None:
    inp = {"authorization": "Bearer abcdefghijklmnop", "model": "x"}
    once = redact(inp)
    twice = redact(once)
    assert once == twice


def test_redact_does_not_mutate_input() -> None:
    inp = {"api_key": "secret", "n": 5}
    redact(inp)
    assert inp["api_key"] == "secret"


def test_redact_handles_deep_recursion_without_crashing() -> None:
    # Make a 50-deep nested dict; redact should bail out, not blow up.
    obj: object = "leaf"
    for _ in range(50):
        obj = {"child": obj}
    out = redact(obj)
    # Top levels still walked + returned (we cap depth, we don't drop the object)
    assert isinstance(out, dict)


def test_redact_handles_unknown_types_safely() -> None:
    class Custom:
        pass

    c = Custom()
    assert redact(c) is c  # unknown type → pass-through, no crash
