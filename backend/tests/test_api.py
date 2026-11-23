from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

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
