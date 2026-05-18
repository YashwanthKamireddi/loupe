"""LlamaIndex integration — capture every QueryEngine.query / aquery call.

LlamaIndex is the dominant RAG framework in the Python ecosystem. Its main
operation is `QueryEngine.query(query_str)` which retrieves relevant
documents and asks an LLM to synthesize an answer. We patch the query
methods on the base class so every subclass (VectorStoreIndex,
KnowledgeGraphIndex, custom engines) is captured automatically.

Usage:
    from loupe import trace
    from loupe.integrations.llama_index import patch
    patch()

    from llama_index.core import VectorStoreIndex
    index = VectorStoreIndex.from_documents([...])
    qe = index.as_query_engine()

    @trace(framework="llama-index")
    def search(q: str) -> str:
        return str(qe.query(q))
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
    """Monkey-patch llama_index BaseQueryEngine.query / aquery. Idempotent."""
    try:
        # llama-index >= 0.10 split into core; older was monolithic.
        try:
            from llama_index.core.query_engine import (
                BaseQueryEngine,  # type: ignore[import-not-found]
            )
        except ImportError:
            from llama_index.indices.query.base import (
                BaseQueryEngine,  # type: ignore[import-not-found]
            )
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "loupe.integrations.llama_index.patch() needs the `llama-index` package. "
            "Install with: pip install llama-index"
        ) from exc

    changed = False
    query_fn = getattr(BaseQueryEngine, "query", None)
    if query_fn is not None and not getattr(query_fn, _PATCHED_FLAG, False):
        BaseQueryEngine.query = _wrap_sync(query_fn)  # type: ignore[method-assign]
        changed = True
    aquery_fn = getattr(BaseQueryEngine, "aquery", None)
    if aquery_fn is not None and not getattr(aquery_fn, _PATCHED_FLAG, False):
        BaseQueryEngine.aquery = _wrap_async(aquery_fn)  # type: ignore[method-assign]
        changed = True
    return changed


def _wrap_sync(original: Any) -> Any:
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


def _wrap_async(original: Any) -> Any:
    @functools.wraps(original)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.time()
        error: BaseException | None = None
        result: Any = None
        try:
            result = await original(self, *args, **kwargs)
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            _emit(self, args, kwargs, result, error, started)

    setattr(wrapper, _PATCHED_FLAG, True)
    return wrapper


def _emit(
    engine: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    result: Any,
    error: BaseException | None,
    started: float,
) -> None:
    t = current_trace()
    if t is None:
        return

    # query_str is the first positional arg or `query_bundle` kwarg
    query_str = kwargs.get("query_str") or kwargs.get("str_or_query_bundle")
    if query_str is None and args:
        query_str = args[0]

    inputs: dict[str, Any] = {
        "query": redact(_short(query_str)),
        "engine": type(engine).__name__,
    }

    outputs: dict[str, Any] = {}
    if result is not None:
        # Response objects expose .response and .source_nodes
        outputs["text"] = _short(getattr(result, "response", None) or str(result))
        sources = getattr(result, "source_nodes", None)
        if isinstance(sources, list):
            outputs["source_count"] = len(sources)

    t.add_step(
        Step(
            step_id=uuid.uuid4().hex[:12],
            parent_step_id=None,
            kind="tool-call",  # RAG queries are tool-like — retrieve + synthesize
            name=f"llama-index:{type(engine).__name__}",
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
