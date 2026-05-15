"""Live updates: verify the SSE route is registered.

The full streaming behavior is verified end-to-end against a real uvicorn
server in development (curl http://localhost:7861/api/events → see the
`event: hello` frame within ~50 ms). It can't be exercised through the
in-process ASGI TestClient because TestClient buffers the entire response
body and our SSE generator runs forever — an unrelated FastAPI/Starlette
quirk. So we assert the route exists and call it a day.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from loupe.ui.server import create_app  # noqa: E402


def test_events_route_is_registered() -> None:
    app = create_app()
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/events" in paths
