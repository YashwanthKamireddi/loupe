"""Cross-package version parity check.

Both the Python SDK (`_version.__version__`) and the TypeScript SDK
(`packages/loupe-ts/package.json` + `src/index.ts::VERSION`) MUST publish
under the same version string. They share a wire format — a version skew
silently signals "incompatible" to users who pin one and not the other.

This test runs from the loupe-py package, walks up to the monorepo root,
and asserts all three version sources agree. If you're bumping a version,
bump all three at once.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from loupe._version import __version__ as py_version


def _monorepo_root() -> Path:
    """Walk up until we find packages/ alongside this checkout."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "packages" / "loupe-ts").is_dir():
            return parent
    raise RuntimeError("could not locate monorepo root from " + str(here))


def test_python_and_typescript_versions_match() -> None:
    """py _version, ts package.json, ts src/index.ts VERSION all agree."""
    root = _monorepo_root()
    ts_pkg = json.loads((root / "packages" / "loupe-ts" / "package.json").read_text())
    ts_pkg_version = ts_pkg["version"]

    ts_src = (root / "packages" / "loupe-ts" / "src" / "index.ts").read_text()
    match = re.search(r'export\s+const\s+VERSION\s*=\s*"([^"]+)"', ts_src)
    assert match, "could not find `export const VERSION = \"...\"` in loupe-ts/src/index.ts"
    ts_src_version = match.group(1)

    assert py_version == ts_pkg_version == ts_src_version, (
        f"version drift detected:\n"
        f"  loupe-py     _version.__version__   = {py_version!r}\n"
        f"  loupe-ts     package.json::version  = {ts_pkg_version!r}\n"
        f"  loupe-ts     src/index.ts::VERSION  = {ts_src_version!r}\n"
        f"bump all three together before releasing."
    )
