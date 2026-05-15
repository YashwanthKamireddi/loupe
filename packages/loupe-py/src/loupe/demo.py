"""`loupe demo` — populate a few realistic-looking sample traces.

Solves the empty-dashboard problem on first install: after `pip install loupe`
the user runs `loupe demo` and gets three traces to play with immediately —
one happy path, one destructive-failure, one slow-tool-call. Each one is
pre-tagged so the LoupeBench workflow is visible too.
"""

from __future__ import annotations

import contextlib
import time

from loupe import record_step, trace
from loupe.annotation import Annotation, AnnotationStore
from loupe.store import JSONLStore, default_store


def _seed_happy_path() -> str | None:
    @trace(framework="demo", name="happy-summary-agent")
    def run() -> str:
        record_step("thought", "plan", outputs={"plan": "1. read 2. summarize"})
        record_step(
            "tool-call",
            "read_doc",
            inputs={"path": "docs/INTRO.md"},
            outputs={"chars": 4200},
        )
        record_step(
            "llm-call",
            "claude-haiku-4-5",
            inputs={"prompt": "summarize the document"},
            outputs={
                "text": "Loupe is an open-source forensics layer for LLM agents.",
                "input_tokens": 1200,
                "output_tokens": 18,
                "stop_reason": "end_turn",
            },
        )
        return "ok"

    run()
    return _latest_trace_id()


def _seed_failure() -> tuple[str | None, str | None]:
    captured: dict[str, str] = {}

    @trace(framework="demo", name="auth-refactor-agent")
    def run() -> str:
        record_step("thought", "plan", outputs={"plan": "1. read auth.py 2. apply diff"})
        record_step("tool-call", "read_file", inputs={"path": "src/auth.py"})
        record_step(
            "llm-call",
            "claude-sonnet-4-6",
            inputs={"prompt": "refactor auth.py to use jose"},
            outputs={
                "text": "I will now delete the old file. rm -rf src/",
                "input_tokens": 980,
                "output_tokens": 14,
            },
        )
        bad = record_step(
            "error",
            "unguarded-delete",
            error="rm -rf src/ instead of src/auth_old.py",
            metadata={"severity": "critical"},
        )
        if bad:
            captured["step"] = bad.step_id
        raise RuntimeError(
            "unguarded-delete: agent attempted `rm -rf src/` instead of `rm src/auth_old.py`"
        )

    with contextlib.suppress(RuntimeError):
        run()
    return _latest_trace_id(), captured.get("step")


def _seed_slow_tool() -> str | None:
    @trace(framework="demo", name="data-loader-agent")
    def run() -> str:
        record_step("thought", "plan", outputs={"plan": "fetch then summarize"})
        record_step(
            "tool-call",
            "http_get",
            inputs={"url": "https://example.com/big.json"},
            outputs={"bytes": 4_812_000, "status": 200},
            metadata={"elapsed_ms": 2400},
        )
        record_step(
            "llm-call",
            "gpt-4o-mini",
            inputs={"prompt": "summarize the dataset"},
            outputs={
                "text": "Dataset is a 4.8MB JSON dump of recent OSS releases.",
                "prompt_tokens": 980,
                "completion_tokens": 22,
                "finish_reason": "stop",
            },
        )
        return "done"

    run()
    return _latest_trace_id()


def _latest_trace_id() -> str | None:
    store = default_store()
    if not isinstance(store, JSONLStore):
        return None
    files = sorted(store.root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0].stem if files else None


def seed(*, tag_failure: bool = True) -> list[str]:
    """Create the three demo traces. Returns trace_ids in seeding order.

    If `tag_failure` is True, also writes a pre-baked annotation on the
    failing step so the LoupeBench workflow is visible immediately.
    """
    ids: list[str] = []
    if (h := _seed_happy_path()):
        ids.append(h)
    time.sleep(0.001)  # ensure mtimes order if filesystem is coarse
    fail_id, fail_step = _seed_failure()
    if fail_id:
        ids.append(fail_id)
    time.sleep(0.001)
    if (s := _seed_slow_tool()):
        ids.append(s)

    if tag_failure and fail_id and fail_step:
        AnnotationStore().add(
            Annotation(
                trace_id=fail_id,
                step_id=fail_step,
                failure_category="unguarded-delete",
                notes=(
                    "Agent issued `rm -rf src/` instead of `rm src/auth_old.py`. "
                    "The LLM treated the cleanup step as 'delete everything stale' "
                    "without consulting the file plan it drafted earlier."
                ),
                mitigation=(
                    "Wrap rm in a tool guard that refuses any path above the working "
                    "set, and require a one-token confirmation prompt for destructive ops."
                ),
                severity="critical",
                annotator="loupe-demo",
                tags=["coding", "file-io"],
            )
        )
    return ids
