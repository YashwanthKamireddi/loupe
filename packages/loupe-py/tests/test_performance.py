"""Performance benchmark — proves @trace overhead is negligible.

The contract: instrumenting an agent with Loupe MUST NOT meaningfully slow
it down. This test asserts hard bounds:

  - Per-step overhead is under 100 microseconds on average
  - Whole-trace overhead (begin → finish + save) is under 5 milliseconds
    for traces of typical size (~10 steps)

If anyone changes the hot path in a way that regresses performance, this
test fails the build and they have to argue for the regression.
"""

from __future__ import annotations

import time
from pathlib import Path

from loupe import record_step, trace
from loupe.store import JSONLStore


def test_record_step_overhead_under_100us(tmp_path: Path) -> None:
    """Calling record_step inside an active trace must average < 100µs."""
    store = JSONLStore(root=tmp_path)
    iterations = 1000

    @trace(framework="perf", name="bench", store=store)
    def bench() -> None:
        for i in range(iterations):
            record_step("thought", "step", outputs={"i": i})

    started = time.perf_counter()
    bench()
    elapsed = time.perf_counter() - started

    # Cost includes 1000 record_step calls + 1 disk write at the end.
    per_step_us = (elapsed * 1_000_000) / iterations
    assert per_step_us < 100, (
        f"record_step averaged {per_step_us:.1f}µs per call "
        f"(budget: 100µs). Hot path regressed."
    )


def test_whole_trace_overhead_under_5ms(tmp_path: Path) -> None:
    """A small trace (10 steps) + disk write must finish in under 5 ms."""
    store = JSONLStore(root=tmp_path)
    samples = []

    @trace(framework="perf", name="bench-small", store=store)
    def bench() -> None:
        for i in range(10):
            record_step("thought", f"step-{i}")

    # Warm up so the venv cache is hot and the import-time cost doesn't count.
    bench()

    # Take the median of 20 runs so we don't fail on a one-off GC pause.
    for _ in range(20):
        started = time.perf_counter()
        bench()
        samples.append(time.perf_counter() - started)
    samples.sort()
    median = samples[len(samples) // 2]
    assert median < 0.005, (
        f"Median trace time: {median * 1000:.2f}ms (budget: 5ms). "
        f"The full distribution: {[f'{s * 1000:.2f}ms' for s in samples]}"
    )


def test_no_active_trace_overhead_is_a_single_dict_get(tmp_path: Path) -> None:
    """If there's no active @trace, record_step is essentially a no-op.

    The whole call should average under 5 microseconds — a single ContextVar
    lookup + a None check.
    """
    iterations = 10_000
    started = time.perf_counter()
    for _ in range(iterations):
        # No active trace — should return None immediately.
        assert record_step("thought", "no-op") is None
    elapsed = time.perf_counter() - started
    per_call_us = (elapsed * 1_000_000) / iterations
    assert per_call_us < 5, (
        f"No-trace record_step averaged {per_call_us:.2f}µs (budget: 5µs)."
    )
