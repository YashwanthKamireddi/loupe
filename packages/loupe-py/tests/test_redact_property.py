"""Property-based fuzz tests for the redactor.

`redact()` is in the hot path of every captured payload — every Loupe trace
ever shipped to disk passes through it. The unit tests cover known shapes;
these hypothesis tests cover the long tail of weird inputs nobody thinks to
write tests for (deeply nested empty dicts, lists of mixed types, unicode
keys, the int 0, etc.).

The contract being fuzzed is the stability rules from `_redact.py`:
  1. Never raises on any JSON-like input.
  2. Idempotent: redact(redact(x)) == redact(x).
  3. Non-mutating: the input is untouched.
  4. Type-preserving: dict in → dict out, list in → list out, str in → str out.
  5. Depth-capped: no stack overflow on deeply nested inputs.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from loupe._redact import redact  # noqa: E402

# A JSON-shaped value recursively built from primitives and containers.
json_value = st.recursive(
    base=st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(2**31), max_value=2**31 - 1),
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        st.text(max_size=200),
    ),
    extend=lambda inner: st.one_of(
        st.lists(inner, max_size=8),
        st.dictionaries(st.text(min_size=1, max_size=40), inner, max_size=8),
    ),
    max_leaves=30,
)


@given(value=json_value)
@settings(max_examples=400, deadline=None)
def test_redact_never_raises_on_json_shaped_input(value: Any) -> None:
    # The contract: redact must not throw on any JSON-shaped input.
    redact(value)


@given(value=json_value)
@settings(max_examples=400, deadline=None)
def test_redact_is_idempotent(value: Any) -> None:
    once = redact(value)
    twice = redact(once)
    assert once == twice


@given(value=json_value)
@settings(max_examples=400, deadline=None)
def test_redact_does_not_mutate_input(value: Any) -> None:
    snapshot = copy.deepcopy(value)
    redact(value)
    assert value == snapshot


@given(value=json_value)
@settings(max_examples=200, deadline=None)
def test_redact_preserves_outer_type_shape(value: Any) -> None:
    out = redact(value)
    if isinstance(value, dict):
        assert isinstance(out, dict)
        # Keys are preserved (only VALUES change)
        assert set(out.keys()) == set(value.keys())
    elif isinstance(value, list):
        assert isinstance(out, list)
        assert len(out) == len(value)
    elif isinstance(value, str):
        assert isinstance(out, str)
    elif value is None:
        assert out is None
    elif isinstance(value, bool):  # before int! True is also int
        assert isinstance(out, bool)
    elif isinstance(value, int):
        assert isinstance(out, int)
    elif isinstance(value, float):
        assert isinstance(out, float)


@given(value=json_value)
@settings(max_examples=200, deadline=None)
def test_redact_never_introduces_credentials(value: Any) -> None:
    """If the input contains no credentials AND no '[redacted]' sentinel,
    output shouldn't either. The sentinel-in-input case is a degenerate edge
    we explicitly exclude — the redactor's contract is to never *invent* one."""
    if (
        not _contains_credential(value)
        and not _has_secret_keys(value)
        and not _contains_redacted_string(value)
    ):
        out = redact(value)
        assert not _contains_redacted_string(out), (
            f"redact introduced '[redacted]' into clean input: {value} -> {out}"
        )


# -- helpers --


def _contains_credential(value: Any) -> bool:
    if isinstance(value, str):
        return "Bearer " in value or value.startswith(("sk-", "gho_", "ghp_", "AIza", "eyJ"))
    if isinstance(value, dict):
        return any(_contains_credential(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_credential(v) for v in value)
    return False


def _has_secret_keys(value: Any) -> bool:
    """Use the exact regex the redactor uses — anything else is a footgun."""
    from loupe._redact import _SECRET_NAME_PATTERNS

    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and _SECRET_NAME_PATTERNS.search(k):
                return True
            if _has_secret_keys(v):
                return True
    elif isinstance(value, list):
        return any(_has_secret_keys(v) for v in value)
    return False


def _contains_redacted_string(value: Any) -> bool:
    if isinstance(value, str):
        return "[redacted]" in value
    if isinstance(value, dict):
        return any(_contains_redacted_string(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_redacted_string(v) for v in value)
    return False
