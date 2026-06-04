# Contributing to LoupeBench

LoupeBench is a public, CC-BY-4.0 corpus of LLM-agent failures, with
optional SAE-feature attribution per step. The whole point is that
**any researcher can read a failure, reproduce it, attribute its
circuit, and cite it.**

This guide explains how to add one.

## What a good entry looks like

A LoupeBench entry is one JSONL line in `bench/loupebench-v0.1.jsonl`,
conforming to `bench/loupebench-v0.1.schema.json`. Required keys:

| Key | What it is |
|---|---|
| `id` | `lb-<category>-<NNN>`, e.g. `lb-tool-hallucination-006` |
| `framework` | what produced the run (`react-demo`, `langgraph`, `crewai`, `browser-use`, etc.) |
| `trace` | the parent trace's header (`trace_id`, `name`, `started_at`, `ended_at`, `metadata`) |
| `step` | the offending step in full (`step_id`, `kind`, `name`, `inputs`, `outputs`, `error`, …) |
| `annotation.failure_category` | one of: `hallucination`, `tool-hallucination`, `rate-limit`, `deprecated-model`, `loop`, `format-violation`, `refusal`, `wrong-answer`, `timeout`, `other` |
| `annotation.severity` | `low`, `medium`, `high`, `critical` |
| `annotation.notes` | plain-English explanation. **Be specific** — the model said X, the correct answer is Y, the failure mode is Z. |
| `annotation.mitigation` | what someone debugging this should change. Prompt tweak, code guard, model swap, harness fix — concrete. |
| `annotation.annotator` | your handle or `loupebench-v0.1` for editor entries |
| `annotation.tags` | free-form keywords for clustering |
| `license` | `CC-BY-4.0` (required) |

Optional:

| Key | What it is |
|---|---|
| `annotation.circuit_attribution_hint` | one-line summary of what SAE attribution surfaced, if you ran it |

## The contribution flow

1. **Capture a real run** with Loupe:
   ```bash
   LOUPE_AUTOPATCH=1 python my_agent.py
   loupe list                            # find the trace id
   loupe show <trace>                    # confirm the failure is real
   ```

2. **Tag the offending step:**
   ```bash
   loupe tag <trace> <step> <category>
   loupe annotations <trace>             # confirm it saved
   ```

3. **(Optional but encouraged) Attribute the circuit:**
   ```bash
   loupe attribute <trace> --backend sae --explain
   ```
   This pulls Neuronpedia explanations and stores `top_features`
   inside the annotation. The result feeds the `loupe cluster` view
   and surfaces in this dataset under `circuit_attribution_hint`.

4. **Export your entry:**
   ```bash
   loupe export --format loupebench --trace <trace> --step <step> \
                --out my-entry.jsonl
   ```
   (Or hand-craft a JSON object matching the schema.)

5. **Open a PR** appending one line to
   `bench/loupebench-v0.1.jsonl`. CI validates against
   `loupebench-v0.1.schema.json`.

## Validation

```bash
loupe verify bench/loupebench-v0.1.jsonl   # schema check
loupe bench preview                          # render entries as a table
```

## Anonymization

LoupeBench is public. Strip personal data, API keys, internal product
names, customer prompts. **Do not submit a trace your employer would
not want shared.** The redaction patterns in Loupe (`loupe config
redact.patterns`) catch standard secrets at capture time, but you are
responsible for the final scrub.

## What makes a strong entry

- **Reproducibility.** Someone with the same model + a similar prompt
  should be able to surface the same failure.
- **Specificity.** "agent failed" is not useful. "agent hallucinated
  `event_stream=True` as an httpx kwarg — the real httpx has no SSE
  flag" is.
- **Honest severity.** A flaky retry that succeeded on its own is
  `low`; a wrong-answer that ships to production is `critical`.
- **Circuit attribution wherever possible.** Even one or two named
  features per entry compounds across the corpus — `loupe cluster`
  becomes more useful with each contribution.

## Citation

If LoupeBench helps your research, cite it as:

```
@misc{loupebench2026,
  title  = {LoupeBench: A Public Dataset of Agent Failures with Circuit Attribution},
  author = {Kamireddi, Yashwanth and contributors},
  year   = {2026},
  url    = {https://github.com/YashwanthKamireddi/loupe/tree/main/bench}
}
```
