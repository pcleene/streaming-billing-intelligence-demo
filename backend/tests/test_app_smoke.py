"""Application-level smoke tests: app imports clean, all routers register,
exception mapping converts AcmeError to its declared HTTP status.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.errors import CaseNotFound
from app.main import create_app


def test_app_constructs_and_routes_register() -> None:
    app = create_app()
    paths = {r.path for r in app.routes}
    # Pillar surfaces
    assert "/api/customers" in paths
    assert "/api/customers/{customer_id}" in paths
    assert "/api/quarantine/cases" in paths
    assert "/api/quarantine/cases/{case_id}" in paths
    assert "/api/quarantine/cases/{case_id}/disposition" in paths
    assert "/api/quarantine/cases/{case_id}/assist" in paths
    assert "/api/rules" in paths
    assert "/api/rules/test" in paths
    assert "/api/features/freshness" in paths
    assert "/api/stream" in paths
    assert "/health" in paths


def test_acme_error_maps_to_http_status() -> None:
    """The AcmeError handler should convert subclasses to their http_status."""
    app = create_app()

    @app.get("/_test/notfound")
    async def _raises():  # noqa: D401
        raise CaseNotFound("case missing")

    # Avoid lifespan (no live Mongo in unit tests) by overriding it.
    app.router.lifespan_context = None  # type: ignore[assignment]
    client = TestClient(app)
    resp = client.get("/_test/notfound")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"]
    assert "case missing" in body["error"]["message"]
