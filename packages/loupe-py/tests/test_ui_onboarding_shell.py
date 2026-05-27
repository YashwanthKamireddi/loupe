"""The dashboard's empty-state copy-paste env-var line MUST match the
user's shell. v0.0.66 added GET /api/onboarding for that detection.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from loupe.ui.server import create_app


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("LOUPE_HOME", str(tmp_path))
    return TestClient(create_app())


def test_bash_default(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("SHELL", "/bin/bash")
    res = client.get("/api/onboarding")
    assert res.status_code == 200
    body = res.json()
    assert body["shell"] == "bash"
    assert body["export_template"] == "export {name}={value}"
    assert body["example"] == "export GEMINI_API_KEY=YOUR_KEY"


def test_zsh_uses_bash_template(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    """zsh shares `export NAME=VALUE` syntax with bash."""
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    body = client.get("/api/onboarding").json()
    assert body["export_template"] == "export {name}={value}"


def test_fish(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    body = client.get("/api/onboarding").json()
    assert body["shell"] == "fish"
    assert body["export_template"] == "set -Ux {name} {value}"
    assert body["example"] == "set -Ux GEMINI_API_KEY YOUR_KEY"


def test_powershell(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setenv("SHELL", "/usr/bin/pwsh")
    body = client.get("/api/onboarding").json()
    assert body["shell"] == "powershell"
    assert body["example"] == "$env:GEMINI_API_KEY='YOUR_KEY'"


def test_unset_shell_falls_back_to_bash(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.delenv("COMSPEC", raising=False)
    body = client.get("/api/onboarding").json()
    # Bash is the safe universal fallback.
    assert body["shell"] == "bash"
    assert body["example"] == "export GEMINI_API_KEY=YOUR_KEY"
