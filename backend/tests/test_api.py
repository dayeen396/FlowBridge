from urllib.parse import parse_qs, urlparse
from fastapi.testclient import TestClient
import pytest

from app.database import get_db
from app.main import app


def test_health_is_available_at_root_and_api_prefix():
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/health").json() == {"status": "ok"}


def test_workflow_routes_require_a_signed_in_user():
    client = TestClient(app)

    response = client.get("/api/workflows")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/me", None),
        ("POST", "/api/auth/logout", None),
        ("GET", "/api/workflows", None),
        ("POST", "/api/workflows", {"schema_version": 1, "name": "Demo", "nodes": [{"id": "trigger", "type": "manual_trigger", "name": "Manual"}], "edges": []}),
        ("GET", "/api/workflows/00000000-0000-0000-0000-000000000001", None),
        ("PUT", "/api/workflows/00000000-0000-0000-0000-000000000001", {"version": 1, "schema_version": 1, "name": "Demo", "nodes": [{"id": "trigger", "type": "manual_trigger", "name": "Manual"}], "edges": []}),
        ("DELETE", "/api/workflows/00000000-0000-0000-0000-000000000001", None),
        ("POST", "/api/workflows/00000000-0000-0000-0000-000000000001/run", {}),
        ("GET", "/api/workflows/00000000-0000-0000-0000-000000000001/executions", None),
        ("POST", "/api/workflows/00000000-0000-0000-0000-000000000001/webhook-token/rotate", None),
        ("GET", "/api/executions/00000000-0000-0000-0000-000000000001", None),
        ("GET", "/api/credentials", None),
        ("POST", "/api/credentials", {"name": "SMTP", "type": "smtp", "data": {"password": "secret"}}),
        ("DELETE", "/api/credentials/00000000-0000-0000-0000-000000000001", None),
        ("GET", "/api/mcp-servers", None),
        ("POST", "/api/mcp-servers", {"name": "Tools", "transport": "streamable_http", "url": "https://mcp.example.com/mcp"}),
        ("DELETE", "/api/mcp-servers/00000000-0000-0000-0000-000000000001", None),
        ("POST", "/api/mcp-servers/00000000-0000-0000-0000-000000000001/refresh", None),
        ("GET", "/api/mcp-servers/00000000-0000-0000-0000-000000000001/tools", None),
    ],
)
def test_protected_api_routes_require_authentication(method, path, body):
    response = TestClient(app).request(method, path, json=body)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_github_login_reports_missing_oauth_configuration(monkeypatch):
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)
    client = TestClient(app)

    response = client.get("/api/auth/github/login")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "OAUTH_NOT_CONFIGURED"


def test_github_login_sets_state_and_callback_rejects_mismatch(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-secret")
    client = TestClient(app)

    response = client.get("/api/auth/github/login", follow_redirects=False)

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["client_id"] == ["test-client"]
    assert client.cookies.get("flowbridge_session")

    callback = client.get(
        "/api/auth/github/callback",
        params={"code": "test-code", "state": "not-the-session-state"},
        follow_redirects=False,
    )
    assert callback.status_code == 400
    assert callback.json()["error"]["message"] == "OAuth state validation failed"


def test_node_catalog_route_lists_the_registered_node_types():
    response = TestClient(app).get("/api/node-types")

    assert response.status_code == 200
    assert {item["type"] for item in response.json()} >= {
        "manual_trigger", "webhook", "http_request", "data_processing", "condition", "send_email", "mcp_tool"
    }


def test_webhook_route_returns_not_found_for_unknown_token():
    class EmptyDatabase:
        def scalar(self, _query):
            return None

    def override_db():
        yield EmptyDatabase()

    app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(app).post("/api/hooks/not-a-token", json={"hello": "world"})
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404
