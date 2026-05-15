"""Provider matcher tests — both exact hits and openai-compatible fallback."""

from __future__ import annotations

import pytest

from loupe.integrations._providers import (
    ALL_PROVIDERS,
    detect_provider_from_host,
    looks_like_openai_compatible,
)


@pytest.mark.parametrize(
    "host, expected",
    [
        # Exact frontier matches
        ("api.anthropic.com", "anthropic"),
        ("api.openai.com", "openai"),
        ("generativelanguage.googleapis.com", "gemini"),
        ("api.mistral.ai", "mistral"),
        ("codestral.mistral.ai", "mistral-codestral"),
        ("api.cohere.com", "cohere"),
        ("api.x.ai", "xai"),
        ("api.deepseek.com", "deepseek"),
        # Inference
        ("api.groq.com", "groq"),
        ("api.cerebras.ai", "cerebras"),
        ("api.together.xyz", "together"),
        ("api.fireworks.ai", "fireworks"),
        ("api.perplexity.ai", "perplexity"),
        ("api.replicate.com", "replicate"),
        ("api.hyperbolic.xyz", "hyperbolic"),
        # Aggregators
        ("openrouter.ai", "openrouter"),
        ("api.portkey.ai", "portkey"),
        # Cloud
        ("my-deployment.openai.azure.com", "azure-openai"),
        ("east-1-aiplatform.googleapis.com", "vertex-ai"),
        ("bedrock-runtime.us-east-1.amazonaws.com", "aws-bedrock"),
        # Embedding
        ("api.voyageai.com", "voyage"),
        ("api.jina.ai", "jina"),
        ("api-inference.huggingface.co", "huggingface"),
        ("my-model.endpoints.huggingface.cloud", "huggingface-endpoints"),
        # Local
        ("localhost", "local"),
        ("127.0.0.1", "local-ip"),
    ],
)
def test_host_matching(host: str, expected: str) -> None:
    provider = detect_provider_from_host(host)
    assert provider is not None, f"{host} did not match"
    assert provider.label == expected


def test_subdomain_match() -> None:
    """A subdomain of a known suffix should match the same provider."""
    p = detect_provider_from_host("eu.api.anthropic.com")
    assert p is not None and p.label == "anthropic"


def test_unknown_host_no_match() -> None:
    assert detect_provider_from_host("api.example.com") is None
    assert detect_provider_from_host(None) is None
    assert detect_provider_from_host("") is None


def test_case_insensitive_match() -> None:
    p = detect_provider_from_host("API.ANTHROPIC.COM")
    assert p is not None and p.label == "anthropic"


def test_openai_compatible_detection() -> None:
    assert looks_like_openai_compatible({
        "model": "anything",
        "messages": [{"role": "user", "content": "hi"}],
    })
    # Missing messages
    assert not looks_like_openai_compatible({"model": "x"})
    # Missing model
    assert not looks_like_openai_compatible({"messages": []})
    # Wrong type
    assert not looks_like_openai_compatible("not a dict")
    assert not looks_like_openai_compatible(None)
    assert not looks_like_openai_compatible({"messages": "not a list", "model": "x"})


def test_all_providers_have_unique_labels() -> None:
    labels = [p.label for p in ALL_PROVIDERS]
    assert len(labels) == len(set(labels)), "duplicate provider label found"


def test_provider_count_is_substantial() -> None:
    """We should cover the 2026 ecosystem broadly."""
    assert len(ALL_PROVIDERS) >= 40, (
        f"only {len(ALL_PROVIDERS)} providers — the 2026 ecosystem is much larger"
    )
