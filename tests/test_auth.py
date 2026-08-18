from fastapi.testclient import TestClient

from app.main import app


def test_health_is_public():
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200


def test_home_redirects_to_login():
    with TestClient(app) as client:
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_api_rejects_unauthenticated_requests():
    with TestClient(app) as client:
        response = client.get("/api/opportunities")
        assert response.status_code == 401


def test_local_login_in_development():
    with TestClient(app) as client:
        response = client.post(
            "/login",
            data={"username": "admin", "password": "development-only"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert client.get("/").status_code == 200


def test_google_login_requires_configuration():
    with TestClient(app) as client:
        response = client.get("/auth/google", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login?oauth=unavailable"
