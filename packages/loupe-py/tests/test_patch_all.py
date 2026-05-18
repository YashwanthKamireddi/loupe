"""patch_all() — single-call instrumentation of every available integration."""

from __future__ import annotations

import sys

from loupe.integrations import patch_all


def test_patch_all_returns_dict() -> None:
    """patch_all always returns a dict, even when nothing is installed."""
    # Wipe any prior import-time patches so this test reflects a fresh state.
    for mod in list(sys.modules):
        if mod.startswith("loupe.integrations.") and not mod.endswith("__init__"):
            sys.modules.pop(mod, None)

    report = patch_all()
    assert isinstance(report, dict)


def test_patch_all_only_reports_installed_frameworks() -> None:
    """If `unicorns_ai` isn't on PyPI, it MUST NOT appear in the report."""
    report = patch_all()
    assert "unicorns_ai" not in report
    assert "fictional-framework" not in report


def test_patch_all_is_idempotent() -> None:
    """Calling patch_all twice should produce the same set of keys."""
    first = patch_all()
    second = patch_all()
    assert set(first.keys()) == set(second.keys())
    # On the second call, every value should be False (already patched).
    for value in second.values():
        assert value is False


def test_patch_all_picks_up_available_integrations() -> None:
    """Any integration whose dep is importable should appear in the report.

    We use a fresh subprocess so other tests' sys.modules pollution
    (fake httpx, fake anthropic, etc.) doesn't bleed into this assertion.
    """
    import subprocess

    code = (
        "from loupe.integrations import patch_all\n"
        "import importlib, sys\n"
        "try:\n"
        "    importlib.import_module('httpx')\n"
        "    have_httpx = True\n"
        "except ImportError:\n"
        "    have_httpx = False\n"
        "report = patch_all()\n"
        "if have_httpx:\n"
        "    assert 'universal-httpx' in report, report\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
