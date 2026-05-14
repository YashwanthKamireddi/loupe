# Contributing to Loupe

Loupe is a research-leaning OSS project. Most contributions land through one of three doors:

## 1. Annotate a failure for LoupeBench

The single highest-leverage contribution. Run any agent (LangGraph, OpenHands, Claude Code), let it fail, then:

```bash
pip install loupe
loupe ui                 # tag the failing step inline
loupe export --out my-failures.jsonl
```

Open a PR adding `my-failures.jsonl` under `bench/data/`. Quality bar: a real failure (not synthetic), reproducible, with a clear root-cause note.

## 2. Add a new framework integration

Each integration lives in `packages/loupe-py/src/loupe/integrations/<framework>.py`. The contract is small: import the framework lazily, hook into its events, call `record_step` (or build Step objects directly) against the active trace from `loupe.current_trace()`.

Look at `loupe/integrations/langchain.py` as the reference — it pairs `*_start`/`*_end` events using LangChain run IDs and handles errors.

## 3. Improve the dashboard or annotation taxonomy

The UI lives in `packages/loupe-py/src/loupe/ui/`. It's a single FastAPI app + static HTML/CSS/JS (no build step on purpose — easy to hack on).

Failure-category enum lives in `loupe/annotation.py::FailureCategory`. Propose additions in an issue first.

## Dev setup

```bash
cd packages/loupe-py
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,ui,langgraph]'

python -m ruff check src/ tests/    # lint
python -m pytest -q                 # tests (25 currently)
loupe ui                            # open http://127.0.0.1:7860
```

## Style

- Type hints required on new public APIs.
- `from __future__ import annotations` everywhere.
- No emojis in code or commits.
- Commit messages: imperative present tense, scope first (`loupe-py:`, `ui:`, `bench:`).

## License

By contributing you agree your code is released under the MIT license (see `LICENSE`).
