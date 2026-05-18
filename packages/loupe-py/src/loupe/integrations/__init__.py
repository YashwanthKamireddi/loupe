"""Framework-specific instrumentation for Loupe.

Each submodule registers hooks into one agent framework so existing user code
needs no manual `record_step` calls. The framework module is only imported
when the user opts in — keeping `loupe` core dependency-free.

The `patch_all()` helper turns on every integration whose dependency is
already installed. Missing packages are skipped silently. Returns a dict
mapping integration name → bool (True if patched this call, False if it was
already patched or not available).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

# Each entry: (cli-visible name, module path, predicate-package to importlib-check first).
# Order matters only for cosmetics in the report.
_INTEGRATIONS: list[tuple[str, str, str]] = [
    ("langchain", "loupe.integrations.langchain", "langchain_core"),
    ("anthropic", "loupe.integrations.anthropic", "anthropic"),
    ("openai", "loupe.integrations.openai", "openai"),
    ("pydantic-ai", "loupe.integrations.pydantic_ai", "pydantic_ai"),
    ("llama-index", "loupe.integrations.llama_index", "llama_index"),
    ("dspy", "loupe.integrations.dspy", "dspy"),
    ("crewai", "loupe.integrations.crewai", "crewai"),
    ("autogen", "loupe.integrations.autogen", "autogen"),
    ("universal-httpx", "loupe.integrations.httpx", "httpx"),
]


def patch_all() -> dict[str, bool]:
    """Turn on every integration whose dependency is installed. Idempotent.

    Returns a dict like {"anthropic": True, "openai": False, ...} where the
    bool tells you whether this call actually changed anything for that one.
    Integrations whose dependency package isn't installed are absent entirely.

    Example:
        from loupe.integrations import patch_all
        report = patch_all()
        # {'langchain': True, 'universal-httpx': True}
    """
    report: dict[str, bool] = {}
    for name, module_path, dep_pkg in _INTEGRATIONS:
        # Skip if the framework itself isn't installed
        try:
            importlib.import_module(dep_pkg)
        except ImportError:
            continue

        # langchain integration is special — it doesn't expose patch()
        # (it provides a callback handler instead). Don't include it here.
        if name == "langchain":
            continue

        try:
            mod = importlib.import_module(module_path)
            patch: Callable[[], bool] | None = getattr(mod, "patch", None)
            if patch is not None:
                report[name] = bool(patch())
        except ImportError:
            # The integration module exists but its own deps don't
            continue
    return report
