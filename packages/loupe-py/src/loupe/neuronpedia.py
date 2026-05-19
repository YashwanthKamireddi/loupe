"""Neuronpedia client — fetch human-readable explanations for SAE features.

Neuronpedia (https://neuronpedia.org) hosts auto-generated and
hand-curated interpretations for the SAE feature dictionaries Loupe
already uses for attribution. Without these explanations, an
attribution result is just "feature #23123 fired hard" — useful for
clustering, useless for human review. With them, that same line reads
"feature #23123 (phrases related to legal documents and rulings)".

Design rules
------------
1. **Best-effort.** The Neuronpedia API can be slow or down; nothing
   in Loupe blocks on it. Failed lookups return ``None`` and the
   caller renders the bare feature id.
2. **Local cache.** Explanations are immutable per (release, layer,
   feature). We cache to ``~/.loupe/neuronpedia-cache.json`` so a
   second ``--explain`` run is instant + offline.
3. **Bounded blast radius.** ``looku
p_many`` uses a small thread pool
   so a 16-feature attribution finishes in a couple of seconds.
4. **No mandatory dependency.** Uses httpx, which loupe already pulls
   in via the universal-httpx integration. Falls back gracefully if
   httpx is somehow absent.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from loupe.store import _default_dir

logger = logging.getLogger("loupe.neuronpedia")

# Mapping from the SAE hook name we capture (e.g. blocks.6.hook_resid_pre)
# to the Neuronpedia layer slug (e.g. 6-res-jb). The "set" part (res-jb)
# comes from the SAE release the user attributed with.
_HOOK_PATTERN = re.compile(r"^blocks\.(\d+)\.hook_(\w+)$")


def hook_to_neuronpedia_layer(hook_name: str, release: str) -> str | None:
    """Translate (hook_name, release) → Neuronpedia layer slug.

    e.g. ``hook_to_neuronpedia_layer("blocks.6.hook_resid_pre",
    "gpt2-small-res-jb/blocks.6.hook_resid_pre") == "6-res-jb"``.
    """
    m = _HOOK_PATTERN.match(hook_name)
    if not m:
        return None
    layer_idx = m.group(1)
    # The release id usually carries the set suffix — pull "res-jb" out of
    # "gpt2-small-res-jb" / "gpt2-small-res-jb/blocks.6.hook_resid_pre".
    set_part = _release_set(release)
    if not set_part:
        return None
    return f"{layer_idx}-{set_part}"


def _release_set(release: str) -> str | None:
    """Extract the SAE 'set' tag from a release id.

    We currently understand the Joseph Bloom (``res-jb``) and
    Feature-splitting (``res-jb-feature-splitting``) releases for
    gpt2-small. Anything else returns ``None`` and the caller skips
    Neuronpedia lookup gracefully.
    """
    base = release.split("/", 1)[0]
    if "-feature-splitting" in base:
        return "res-jb-feature-splitting"
    if "res-jb" in base:
        return "res-jb"
    return None


def model_for_neuronpedia(release: str) -> str | None:
    """Map a SAE release to the Neuronpedia model id.

    Today only the gpt2-small releases we ship as defaults are mapped.
    Extend as new SAE families land.
    """
    if release.startswith("gpt2-small") or "gpt2-small" in release.split("/", 1)[0]:
        return "gpt2-small"
    return None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class _Cache:
    """Tiny JSON file cache for feature explanations.

    Single-file, optimistic concurrency: we re-read on hit-miss and write
    only on add. Concurrent additions across threads serialize through
    an in-process lock; cross-process races may rewrite the same entry
    but never produce torn JSON because we use atomic rename.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._mem: dict[str, str | None] = self._load()

    def _load(self) -> dict[str, str | None]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def get(self, key: str) -> tuple[bool, str | None]:
        """Return (found, value). ``found`` distinguishes 'we cached None'
        (meaning Neuronpedia confirmed no explanation) from 'never looked'."""
        if key in self._mem:
            return True, self._mem[key]
        return False, None

    def put(self, key: str, value: str | None) -> None:
        with self._lock:
            self._mem[key] = value
            self._flush()

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._mem), encoding="utf-8")
        os.replace(tmp, self.path)


def _default_cache() -> _Cache:
    return _Cache(_default_dir() / "neuronpedia-cache.json")


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


_API_BASE = "https://www.neuronpedia.org/api/feature"
_TIMEOUT_SEC = 15.0


def _fetch_one(
    *,
    model_id: str,
    layer: str,
    feature_id: int,
    timeout: float = _TIMEOUT_SEC,
) -> str | None:
    """Single feature lookup. Returns the best explanation text, or None."""
    try:
        import httpx
    except ImportError:
        logger.debug("httpx not installed; skipping Neuronpedia lookup")
        return None
    url = f"{_API_BASE}/{model_id}/{layer}/{feature_id}"
    try:
        r = httpx.get(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug("Neuronpedia fetch failed: %s", exc)
        return None
    if r.status_code != 200:
        return None
    try:
        data: dict[str, Any] = r.json()
    except Exception:  # noqa: BLE001
        return None
    explanations = data.get("explanations") or []
    if not isinstance(explanations, list) or not explanations:
        return None
    # Prefer human-curated explanations over auto-generated; fall back to first.
    for ex in explanations:
        if not isinstance(ex, dict):
            continue
        desc = ex.get("description")
        if not isinstance(desc, str) or not desc.strip():
            continue
        return desc.strip()
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def explain(
    feature_id: int,
    *,
    hook_name: str,
    release: str,
    cache: _Cache | None = None,
) -> str | None:
    """Return a human-readable explanation for one SAE feature, or ``None``."""
    if os.environ.get("LOUPE_DISABLE_NEURONPEDIA"):
        return None
    model_id = model_for_neuronpedia(release)
    layer = hook_to_neuronpedia_layer(hook_name, release)
    if not model_id or not layer:
        return None

    c = cache or _default_cache()
    key = f"{model_id}/{layer}/{feature_id}"
    found, value = c.get(key)
    if found:
        return value
    fetched = _fetch_one(model_id=model_id, layer=layer, feature_id=feature_id)
    c.put(key, fetched)
    return fetched


def explain_many(
    feature_ids: list[int],
    *,
    hook_name: str,
    release: str,
    cache: _Cache | None = None,
    max_workers: int = 8,
) -> dict[int, str | None]:
    """Look up many features in parallel. Honors the cache.

    Returns a ``{feature_id: explanation_or_None}`` mapping. Order
    preserved by feature_id. Never raises — every lookup that fails
    just maps to ``None``.
    """
    if not feature_ids:
        return {}
    if os.environ.get("LOUPE_DISABLE_NEURONPEDIA"):
        return {fid: None for fid in feature_ids}

    c = cache or _default_cache()
    model_id = model_for_neuronpedia(release)
    layer = hook_to_neuronpedia_layer(hook_name, release)
    if not model_id or not layer:
        return {fid: None for fid in feature_ids}

    out: dict[int, str | None] = {}
    pending: list[int] = []
    for fid in feature_ids:
        found, value = c.get(f"{model_id}/{layer}/{fid}")
        if found:
            out[fid] = value
        else:
            pending.append(fid)

    if pending:
        # Bounded concurrency: a 16-feature attribution should finish
        # in ~1-2s on a warm network, never block the CLI.
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _fetch_one,
                    model_id=model_id,
                    layer=layer,
                    feature_id=fid,
                ): fid
                for fid in pending
            }
            for fut in futures:
                fid = futures[fut]
                try:
                    value = fut.result()
                except Exception:  # noqa: BLE001
                    value = None
                c.put(f"{model_id}/{layer}/{fid}", value)
                out[fid] = value

    return out
