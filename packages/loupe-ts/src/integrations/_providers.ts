/**
 * The canonical list of LLM providers Loupe auto-detects. Mirror of the
 * Python `_providers.py`; keep them in sync.
 *
 * Entries are ordered by 2026 popularity. The matcher returns the *first*
 * hit — put more-specific subdomains before broader ones.
 */

export type ProviderCategory =
  | "frontier"
  | "inference"
  | "aggregator"
  | "cloud"
  | "embedding"
  | "local";

export interface Provider {
  label: string;
  hostSuffix: string;
  name: string;
  category: ProviderCategory;
  homepage?: string;
  /**
   * For providers that embed their identifier in the middle of the FQDN
   * (AWS regions, Vertex AI regions), set this to "contains".
   */
  matchStrategy?: "suffix" | "contains";
}

const FRONTIER: Provider[] = [
  { label: "anthropic", hostSuffix: "api.anthropic.com", name: "Anthropic", category: "frontier", homepage: "https://anthropic.com" },
  { label: "openai", hostSuffix: "api.openai.com", name: "OpenAI", category: "frontier", homepage: "https://openai.com" },
  { label: "gemini", hostSuffix: "generativelanguage.googleapis.com", name: "Google Gemini", category: "frontier", homepage: "https://ai.google.dev" },
  { label: "mistral", hostSuffix: "api.mistral.ai", name: "Mistral", category: "frontier", homepage: "https://mistral.ai" },
  { label: "mistral-codestral", hostSuffix: "codestral.mistral.ai", name: "Mistral Codestral", category: "frontier" },
  { label: "cohere", hostSuffix: "api.cohere.com", name: "Cohere", category: "frontier", homepage: "https://cohere.com" },
  { label: "cohere-legacy", hostSuffix: "api.cohere.ai", name: "Cohere (legacy)", category: "frontier" },
  { label: "xai", hostSuffix: "api.x.ai", name: "xAI Grok", category: "frontier", homepage: "https://x.ai" },
  { label: "deepseek", hostSuffix: "api.deepseek.com", name: "DeepSeek", category: "frontier", homepage: "https://deepseek.com" },
  { label: "ai21", hostSuffix: "api.ai21.com", name: "AI21 Labs", category: "frontier", homepage: "https://ai21.com" },
  { label: "reka", hostSuffix: "api.reka.ai", name: "Reka", category: "frontier", homepage: "https://reka.ai" },
  { label: "aleph-alpha", hostSuffix: "api.aleph-alpha.com", name: "Aleph Alpha", category: "frontier" },
  { label: "zhipu", hostSuffix: "open.bigmodel.cn", name: "Zhipu GLM", category: "frontier" },
  { label: "baidu", hostSuffix: "aip.baidubce.com", name: "Baidu ERNIE", category: "frontier" },
  { label: "alibaba", hostSuffix: "dashscope.aliyuncs.com", name: "Alibaba Qwen", category: "frontier" },
];

const INFERENCE: Provider[] = [
  { label: "groq", hostSuffix: "api.groq.com", name: "Groq", category: "inference", homepage: "https://groq.com" },
  { label: "cerebras", hostSuffix: "api.cerebras.ai", name: "Cerebras", category: "inference", homepage: "https://cerebras.ai" },
  { label: "sambanova", hostSuffix: "api.sambanova.ai", name: "SambaNova", category: "inference", homepage: "https://sambanova.ai" },
  { label: "together", hostSuffix: "api.together.xyz", name: "Together", category: "inference", homepage: "https://together.ai" },
  { label: "fireworks", hostSuffix: "api.fireworks.ai", name: "Fireworks", category: "inference", homepage: "https://fireworks.ai" },
  { label: "deepinfra", hostSuffix: "api.deepinfra.com", name: "DeepInfra", category: "inference" },
  { label: "hyperbolic", hostSuffix: "api.hyperbolic.xyz", name: "Hyperbolic", category: "inference" },
  { label: "anyscale", hostSuffix: "api.endpoints.anyscale.com", name: "Anyscale Endpoints", category: "inference" },
  { label: "nebius", hostSuffix: "api.studio.nebius.ai", name: "Nebius AI Studio", category: "inference" },
  { label: "lambda", hostSuffix: "api.lambdalabs.com", name: "Lambda Inference", category: "inference" },
  { label: "lepton", hostSuffix: "api.lepton.ai", name: "Lepton AI", category: "inference" },
  { label: "siliconflow", hostSuffix: "api.siliconflow.com", name: "SiliconFlow", category: "inference" },
  { label: "featherless", hostSuffix: "api.featherless.ai", name: "Featherless", category: "inference" },
  { label: "inference-net", hostSuffix: "api.inference.net", name: "Inference.net", category: "inference" },
  { label: "modal", hostSuffix: "modal.com", name: "Modal Labs", category: "inference" },
  { label: "replicate", hostSuffix: "api.replicate.com", name: "Replicate", category: "inference", homepage: "https://replicate.com" },
  { label: "perplexity", hostSuffix: "api.perplexity.ai", name: "Perplexity", category: "inference", homepage: "https://perplexity.ai" },
];

const AGGREGATORS: Provider[] = [
  { label: "openrouter", hostSuffix: "openrouter.ai", name: "OpenRouter", category: "aggregator" },
  { label: "portkey", hostSuffix: "api.portkey.ai", name: "Portkey", category: "aggregator" },
  { label: "kong-ai", hostSuffix: "ai-gateway.konghq.com", name: "Kong AI Gateway", category: "aggregator" },
  { label: "vellum", hostSuffix: "api.vellum.ai", name: "Vellum", category: "aggregator" },
];

const CLOUD: Provider[] = [
  { label: "azure-openai", hostSuffix: "openai.azure.com", name: "Azure OpenAI", category: "cloud" },
  { label: "aws-bedrock", hostSuffix: "bedrock-runtime", name: "AWS Bedrock", category: "cloud", matchStrategy: "contains" },
  { label: "vertex-ai", hostSuffix: "aiplatform.googleapis.com", name: "Google Vertex AI", category: "cloud", matchStrategy: "contains" },
  { label: "watsonx", hostSuffix: "ml.cloud.ibm.com", name: "IBM watsonx", category: "cloud" },
  { label: "databricks", hostSuffix: "cloud.databricks.com", name: "Databricks Model Serving", category: "cloud" },
];

const EMBEDDING: Provider[] = [
  { label: "voyage", hostSuffix: "api.voyageai.com", name: "Voyage AI", category: "embedding" },
  { label: "jina", hostSuffix: "api.jina.ai", name: "Jina AI", category: "embedding" },
  { label: "nomic", hostSuffix: "api-atlas.nomic.ai", name: "Nomic Atlas", category: "embedding" },
  { label: "huggingface", hostSuffix: "api-inference.huggingface.co", name: "HuggingFace Inference", category: "embedding" },
  { label: "huggingface-endpoints", hostSuffix: "endpoints.huggingface.cloud", name: "HuggingFace Endpoints", category: "embedding" },
];

const LOCAL: Provider[] = [
  { label: "local", hostSuffix: "localhost", name: "Local server (Ollama / vLLM / LM Studio / LiteLLM)", category: "local" },
  { label: "local-ip", hostSuffix: "127.0.0.1", name: "Local server (loopback)", category: "local" },
  { label: "local-net", hostSuffix: "0.0.0.0", name: "Local server (any-iface)", category: "local" },
];

export const ALL_PROVIDERS: Provider[] = [
  ...FRONTIER,
  ...INFERENCE,
  ...AGGREGATORS,
  ...CLOUD,
  ...EMBEDDING,
  ...LOCAL,
];

export function detectProviderFromHost(host: string | null | undefined): Provider | null {
  if (!host) return null;
  const h = host.toLowerCase();
  for (const p of ALL_PROVIDERS) {
    const pat = p.hostSuffix.toLowerCase();
    if (p.matchStrategy === "contains") {
      if (h.includes(pat)) return p;
    } else if (h === pat || h.endsWith(`.${pat}`)) {
      return p;
    }
  }
  return null;
}

export function looksLikeOpenAICompatible(body: unknown): boolean {
  if (!body || typeof body !== "object") return false;
  const b = body as Record<string, unknown>;
  return Array.isArray(b.messages) && "model" in b;
}
