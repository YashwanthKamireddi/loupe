"""Agent-script detection for `loupe onboard`.

The onboarding flow runs on the user's REAL project: it scans the
current folder for a likely agent script, so the first trace a new
user sees is their own code, not a canned demo. This module holds the
pure, testable detection logic; the interactive orchestration lives in
``cli.py``.

Scoring is deliberately simple and explainable — every point has a
human-readable reason we can show the user ("imports openai",
"named agent.py") so the detection never feels like a black box.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Substrings that, if present in a file's source, strongly suggest it
# makes LLM calls. Matched against the raw text (cheap + good enough —
# we're ranking candidates, not compiling an AST).
_LLM_SDK_MARKERS: tuple[str, ...] = (
    "anthropic",
    "openai",
    "google.genai",
    "google import genai",
    "langchain",
    "langgraph",
    "litellm",
    "mistralai",
    "groq",
    "cohere",
    "ollama",
    "loupe",          # already-instrumented code is a perfect candidate
)

# Conventional entry-point filenames for an agent / app.
_AGENT_NAMES: frozenset[str] = frozenset(
    {"main.py", "agent.py", "app.py", "bot.py", "run.py", "chat.py"}
)

# Directories we never descend into — noise, vendored code, or our own
# scaffold output.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".venv", "venv", "env", "site-packages", "__pycache__",
        "node_modules", "tests", "test", ".git", "dist", "build",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", "loupe-demo",
    }
)

_MAX_BYTES_SNIFFED = 64_000  # don't read more than 64KB to classify a file


@dataclass(frozen=True)
class AgentCandidate:
    """One ranked candidate agent script found in the project."""

    path: Path
    score: int
    why: str   # short human reason, e.g. "imports openai · named agent.py"


def _score_file(path: Path) -> AgentCandidate | None:
    """Score a single .py file. Returns None if it scores nothing."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:_MAX_BYTES_SNIFFED]
    except OSError:
        return None

    score = 0
    reasons: list[str] = []

    lowered = text.lower()
    sdk_hit = next((m for m in _LLM_SDK_MARKERS if m in lowered), None)
    if sdk_hit:
        score += 10
        reasons.append(f"imports {sdk_hit}")

    if path.name in _AGENT_NAMES:
        score += 5
        reasons.append(f"named {path.name}")

    if "__main__" in text:
        score += 2
        reasons.append("runnable (__main__)")

    if score == 0:
        return None
    return AgentCandidate(path=path, score=score, why=" · ".join(reasons))


def _iter_candidate_paths(root: Path) -> list[Path]:
    """Yield .py files in root + immediate subdirs (depth 1), skipping
    vendored / noise directories."""
    out: list[Path] = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out
    for entry in entries:
        if entry.is_file() and entry.suffix == ".py":
            out.append(entry)
        elif entry.is_dir() and entry.name not in _SKIP_DIRS and not entry.name.startswith("."):
            try:
                out.extend(sorted(p for p in entry.iterdir() if p.is_file() and p.suffix == ".py"))
            except OSError:
                continue
    return out


def detect_agent_scripts(root: Path) -> list[AgentCandidate]:
    """Scan ``root`` (+ depth-1 subdirs) for likely agent scripts.

    Returns candidates ranked highest-score-first. Ties broken by
    shallower path then name, so a top-level ``agent.py`` beats a
    nested one. Empty list when nothing looks like an agent.
    """
    candidates: list[AgentCandidate] = []
    for path in _iter_candidate_paths(root):
        cand = _score_file(path)
        if cand is not None:
            candidates.append(cand)
    candidates.sort(
        key=lambda c: (-c.score, len(c.path.parts), c.path.name)
    )
    return candidates


def looks_like_project(root: Path) -> bool:
    """True if ``root`` looks like a code project worth onboarding into.

    Used by the bare-`loupe` first-run router to decide whether to
    offer onboarding (project folder) vs the plain setup wizard
    (e.g. the user's home directory).
    """
    try:
        for entry in root.iterdir():
            if entry.is_file() and (
                entry.suffix == ".py" or entry.name == "package.json"
            ):
                return True
    except OSError:
        return False
    return False


__all__ = [
    "AgentCandidate",
    "detect_agent_scripts",
    "looks_like_project",
]
