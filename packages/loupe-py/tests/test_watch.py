"""Tests for the ``loupe watch`` Textual live dashboard.

The full visual contract is exercised by Textual's ``app.run_test()``
context — it spins up the app headless, lets us drive keys/clicks, and
exposes the widget tree for assertions. We don't try to snapshot
rendering; we test the data flow.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from loupe._watch import WatchApp


def _write_trace(
    dir_: Path,
    trace_id: str,
    *,
    failed: bool = False,
    started_at: float | None = None,
    provider: str = "openai",
    model: str = "gpt-4o",
    steps: int = 3,
) -> Path:
    """Write a single-trace JSONL fixture that ``WatchApp`` can read."""
    path = dir_ / f"{trace_id}.jsonl"
    header = {
        "_type": "trace",
        "trace_id": trace_id,
        "started_at": started_at if started_at is not None else time.time(),
        "metadata": {"failed": failed, "provider": provider, "model": model},
    }
    lines = [json.dumps(header)]
    for i in range(steps):
        lines.append(json.dumps({
            "_type": "step",
            "name": f"llm-call:{model}",
            "kind": "llm-call",
            "step_id": f"s{i}",
        }))
    path.write_text("\n".join(lines) + "\n")
    return path


async def test_app_mounts_and_lists_traces(tmp_path: Path) -> None:
    """A fresh app reads the traces dir on mount and populates the table."""
    _write_trace(tmp_path, "t1")
    _write_trace(tmp_path, "t2", failed=True)

    app = WatchApp(traces_dir=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#traces")
        assert table.row_count == 2


async def test_app_quits_on_q(tmp_path: Path) -> None:
    """Pressing ``q`` exits the app cleanly."""
    app = WatchApp(traces_dir=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("q")
        # If the binding fired, exit was called and the context unwinds
        # without raising.


async def test_failed_only_filter(tmp_path: Path) -> None:
    """Pressing ``f`` toggles a failed-only view of the table."""
    _write_trace(tmp_path, "t-ok", failed=False)
    _write_trace(tmp_path, "t-fail", failed=True)

    app = WatchApp(traces_dir=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#traces")
        assert table.row_count == 2

        await pilot.press("f")  # toggle failed-only
        await pilot.pause()
        assert table.row_count == 1, "failed-only filter should hide the OK trace"

        await pilot.press("f")  # toggle back
        await pilot.pause()
        assert table.row_count == 2


async def test_new_trace_appears_on_next_tick(tmp_path: Path) -> None:
    """Adding a new JSONL file while running picks up on the next refresh."""
    _write_trace(tmp_path, "first")
    app = WatchApp(traces_dir=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#traces")
        assert table.row_count == 1

        _write_trace(tmp_path, "second")
        # Force the refresh without waiting 500ms.
        app._refresh()  # type: ignore[attr-defined]
        await pilot.pause()
        assert table.row_count == 2


async def test_unreadable_file_is_skipped_not_crashed(tmp_path: Path) -> None:
    """A truncated / non-JSONL file in the traces dir must not crash refresh."""
    _write_trace(tmp_path, "ok")
    # An empty .jsonl with no parsable header is the most likely real failure.
    (tmp_path / "bogus.jsonl").write_text("")
    (tmp_path / "half.jsonl").write_text('{"_type": "trace", "trace_id": "x"')

    app = WatchApp(traces_dir=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#traces")
        # Only the valid trace appears; the two corrupt files are silently
        # dropped (we never crash the dashboard on a bad capture).
        assert table.row_count == 1


async def test_empty_traces_dir_renders_zero_rows(tmp_path: Path) -> None:
    """No traces yet → app mounts cleanly with an empty table."""
    app = WatchApp(traces_dir=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#traces")
        assert table.row_count == 0


def test_module_exports_run_and_app() -> None:
    """The public entry point is callable and the App class is importable."""
    from loupe import _watch

    assert callable(_watch.run)
    assert isinstance(_watch.WatchApp, type)
