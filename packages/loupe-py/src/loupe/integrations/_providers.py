"""The canonical list of LLM providers Loupe auto-detects.

One source of truth for both the Python `loupe.integrations.httpx` patch and
the `loupe providers` CLI command. The TypeScript SDK mirrors this list in
`packages/loupe-ts/src/integrations/_providers.ts` — keep them in sync.

Entries are ordered by approximate 2026 popularity. The matcher walks the list
and returns the *first* hit, so put more-specific subdomains before broader
ones (e.g. `bedrock-runtime.*.amazonaws.com` before generic AWS domains).

Adding a provider:
1. Add an entry below.
2. Mirror it in the TS file.
3. Run `loupe providers` to confirm; run the tests.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    label: str           # short canonical id
    host_suffix: str     # exact host or DNS suffix (without leading dot)
    name: str            # human label for `loupe providers`
    category: str        # frontier | inference | cloud | embedding | local | aggregator
    homepage: str = ""
    # Some providers (AWS regions, Vertex AI regions) embed an identifier in
    # the *middle* of the hostname rather than as a clean DNS suffix. For
    # those, use "contains" so we match `bedrock-runtime.us-east-1.amazonaws.com`
    # without also matching unrelated `lambda.amazonaws.com`.
    match_strategy: str = "suffix"  # "suffix" | "contains"


# ---------------------------------------------------------------------------
# Frontier labs (the model creators)
# ---------------------------------------------------------------------------

FRONTIER: list[Provider] = [
    Provider("anthropic", "api.anthropic.com", "Anthropic", "frontier", "https://anthropic.com"),
    Provider("openai", "api.openai.com", "OpenAI", "frontier", "https://openai.com"),
    Provider("gemini", "generativelanguage.googleapis.com", "Google Gemini", "frontier",
             "https://ai.google.dev"),
    Provider("mistral", "api.mistral.ai", "Mistral", "frontier", "https://mistral.ai"),
    Provider("mistral-codestral", "codestral.mistral.ai", "Mistral Codestral", "frontier"),
    Provider("cohere", "api.cohere.com", "Cohere", "frontier", "https://cohere.com"),
    Provider("cohere-legacy", "api.cohere.ai", "Cohere (legacy)", "frontier"),
    Provider("xai", "api.x.ai", "xAI Grok", "frontier", "https://x.ai"),
    Provider("deepseek", "api.deepseek.com", "DeepSeek", "frontier", "https://deepseek.com"),
    Provider("ai21", "api.ai21.com", "AI21 Labs", "frontier", "https://ai21.com"),
    Provider("reka", "api.reka.ai", "Reka", "frontier", "https://reka.ai"),
    Provider("aleph-alpha", "api.aleph-alpha.com", "Aleph Alpha", "frontier"),
    Provider("zhipu", "open.bigmodel.cn", "Zhipu GLM", "frontier"),
    Provider("baidu", "aip.baidubce.com", "Baidu ERNIE", "frontier"),
    Provider("alibaba", "dashscope.aliyuncs.com", "Alibaba Qwen", "frontier"),
]

# ---------------------------------------------------------------------------
# Inference providers (host open-weight models)
# ---------------------------------------------------------------------------

INFERENCE: list[Provider] = [
    Provider("groq", "api.groq.com", "Groq", "inference", "https://groq.com"),
    Provider("cerebras", "api.cerebras.ai", "Cerebras", "inference", "https://cerebras.ai"),
    Provider("sambanova", "api.sambanova.ai", "SambaNova", "inference", "https://sambanova.ai"),
    Provider("together", "api.together.xyz", "Together", "inference", "https://together.ai"),
    Provider("fireworks", "api.fireworks.ai", "Fireworks", "inference", "https://fireworks.ai"),
    Provider("deepinfra", "api.deepinfra.com", "DeepInfra", "inference"),
    Provider("hyperbolic", "api.hyperbolic.xyz", "Hyperbolic", "inference"),
    Provider("anyscale", "api.endpoints.anyscale.com", "Anyscale Endpoints", "inference"),
    Provider("nebius", "api.studio.nebius.ai", "Nebius AI Studio", "inference"),
    Provider("lambda", "api.lambdalabs.com", "Lambda Inference", "inference"),
    Provider("lepton", "api.lepton.ai", "Lepton AI", "inference"),
    Provider("siliconflow", "api.siliconflow.com", "SiliconFlow", "inference"),
    Provider("featherless", "api.featherless.ai", "Featherless", "inference"),
    Provider("inference-net", "api.inference.net", "Inference.net", "inference"),
    Provider("modal", "modal.com", "Modal Labs", "inference"),
    Provider("replicate", "api.replicate.com", "Replicate", "inference", "https://replicate.com"),
    Provider("perplexity", "api.perplexity.ai", "Perplexity", "inference", "https://perplexity.ai"),
]

# ---------------------------------------------------------------------------
# Aggregator gateways (route to many models)
# ---------------------------------------------------------------------------

AGGREGATORS: list[Provider] = [
    Provider("openrouter", "openrouter.ai", "OpenRouter", "aggregator"),
    Provider("portkey", "api.portkey.ai", "Portkey", "aggregator"),
    Provider("kong-ai", "ai-gateway.konghq.com", "Kong AI Gateway", "aggregator"),
    Provider("vellum", "api.vellum.ai", "Vellum", "aggregator"),
]

# ---------------------------------------------------------------------------
# Cloud-hosted (enterprise giants)
# ---------------------------------------------------------------------------

CLOUD: list[Provider] = [
    Provider("azure-openai", "openai.azure.com", "Azure OpenAI", "cloud"),
    Provider("aws-bedrock", "bedrock-runtime", "AWS Bedrock", "cloud",
             match_strategy="contains"),
    Provider("vertex-ai", "aiplatform.googleapis.com", "Google Vertex AI", "cloud",
             match_strategy="contains"),
    Provider("watsonx", "ml.cloud.ibm.com", "IBM watsonx", "cloud"),
    Provider("databricks", "cloud.databricks.com", "Databricks Model Serving", "cloud"),
]

# ---------------------------------------------------------------------------
# Embedding & retrieval providers
# ---------------------------------------------------------------------------

EMBEDDING: list[Provider] = [
    Provider("voyage", "api.voyageai.com", "Voyage AI", "embedding"),
    Provider("jina", "api.jina.ai", "Jina AI", "embedding"),
    Provider("nomic", "api-atlas.nomic.ai", "Nomic Atlas", "embedding"),
    Provider("huggingface", "api-inference.huggingface.co", "HuggingFace Inference", "embedding"),
    Provider("huggingface-endpoints", "endpoints.huggingface.cloud",
             "HuggingFace Endpoints", "embedding"),
]

# ---------------------------------------------------------------------------
# Local / self-hosted (servers users run on their own boxes)
# ---------------------------------------------------------------------------

LOCAL: list[Provider] = [
    Provider("local", "localhost", "Local server (Ollama / vLLM / LM Studio / LiteLLM)", "local"),
    Provider("local-ip", "127.0.0.1", "Local server (loopback)", "local"),
    Provider("local-net", "0.0.0.0", "Local server (any-iface)", "local"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ALL_PROVIDERS: list[Provider] = (
    FRONTIER + INFERENCE + AGGREGATORS + CLOUD + EMBEDDING + LOCAL
)


def detect_provider_from_host(host: str | None) -> Provider | None:
    """Return the matching provider for a hostname, or None.

    For `match_strategy="suffix"` (the default), a hit means the host equals
    the suffix OR ends with `.{suffix}`. For `match_strategy="contains"`,
    the pattern just has to appear anywhere in the host — used for cloud
    providers where the identifier sits in the middle of the FQDN.

    Order matters — list-defined order is the matching order.
    """
    if not host:
        return None
    host = host.lower()
    for p in ALL_PROVIDERS:
        pat = p.host_suffix.lower()
        if p.match_strategy == "contains":
            if pat in host:
                return p
        elif host == pat or host.endswith("." + pat):
            return p
    return None


def looks_like_openai_compatible(body: object) -> bool:
    """Heuristic: does this request body look like an OpenAI-spec call?

    Catches LiteLLM-style proxies, internal gateways, OpenAI-compatible
    forks (Together, Fireworks et al. already speak OpenAI spec, but their
    URLs are known; this is for the *unknown* hosts).
    """
    if not isinstance(body, dict):
        return False
    return (
        "messages" in body
        and "model" in body
        and isinstance(body.get("messages"), list)
    )
