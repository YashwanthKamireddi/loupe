"""DSPy integration — capture every dspy.Module / dspy.Predict invocation.

DSPy ("Declarative Self-improving Python") models LLM programs as composable
modules. The base class is `dspy.Module` (and subclasses like `Predict`,
`ChainOfThought`, `ReAct`). We patch `Module.__call__` so every program
invocation lands as a Loupe Step regardless of subclass.

Usage:
    from loupe import trace
    from loupe.integrations.dspy import patch
    patch()

    import dspy
    qa = dspy.Predict("question -> answer")

    @trace(framework="dspy")
    def ask(q: str) -> str:
        return qa(question=q).answer
"""

from __future__ import annotations

import functools
import time
import uuid
from typing import Any

from loupe._redact import redact
from loupe.trace import Step, current_trace

_PATCHED_FLAG = "__loupe_patched__"


def patch() -> bool:
    """Monkey-patch dspy.Module.__call__. Idempotent."""
    try:
        import dspy  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "loupe.integrations.dspy.patch() needs the `dspy-ai` package. "
            "Install with: pip install dspy-ai"
        ) from exc

    module_cls = getattr(dspy, "Module", None) or getattr(dspy, "Program", None)
    if module_cls is None:  # pragma: no cover
        raise ImportError(
            "Couldn't find dspy.Module — is your dspy version too old?"
        )

    if getattr(module_cls.__call__, _PATCHED_FLAG, False):
        return False

    module_cls.__call__ = _wrap(module_cls.__call__)
    return True


def _wrap(original: Any) -> Any:
    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.time()
        error: BaseException | None = None
        result: Any = None
        try:
            result = original(self, *args, **kwargs)
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            _emit(self, args, kwargs, result, error, started)

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


def _emit(
    module: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    result: Any,
    error: BaseException | None,
    started: float,
) -> None:
    t = current_trace()
    if t is None:
        return

    inputs: dict[str, Any] = {
        "module": type(module).__name__,
        "kwargs": redact({k: _short(v) for k, v in kwargs.items()}),
    }
    if args:
        inputs["args"] = redact([_short(a) for a in args])

    outputs: dict[str, Any] = {}
    if result is not None:
        # dspy Predictions are Prediction-like objects with attributes
        if hasattr(result, "__dict__"):
            outputs["fields"] = {
                k: _short(v) for k, v in result.__dict__.items()
                if not k.startswith("_")
            }
        else:
            outputs["text"] = _short(result)

    t.add_step(
        Step(
            step_id=uuid.uuid4().hex[:12],
            parent_step_id=None,
            kind="llm-call",
            name=f"dspy:{type(module).__name__}",
            started_at=started,
            ended_at=time.time(),
            inputs=inputs,
            outputs=outputs,
            error=repr(error) if error is not None else None,
        )
    )


def _short(value: Any, *, limit: int = 4000) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…[truncated]"
    try:
        text = str(value)
    except Exception:
        text = f"<{type(value).__name__}>"
    return text if len(text) <= limit else text[:limit] + "…[truncated]"
