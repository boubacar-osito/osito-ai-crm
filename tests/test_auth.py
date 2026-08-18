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


def test_authenticated_user_can_advance_a_lead():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        created = client.post(
            "/api/leads",
            json={
                "name": "Lead Test",
                "headline": "Business Manager IT",
                "company": "ESN Test",
                "linkedin_url": "https://www.linkedin.com/in/missionflow-test/",
                "connected_on": "2026-08-18",
            },
        )
        assert created.status_code == 200
        lead_id = created.json()["id"]
        updated = client.patch(f"/api/leads/{lead_id}/stage", json={"stage": "message_envoye"})
        assert updated.status_code == 200
        assert updated.json()["stage"] == "message_envoye"


def test_invalid_lead_stage_is_rejected():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        response = client.patch("/api/leads/1/stage", json={"stage": "nimporte_quoi"})
        assert response.status_code == 422


def test_coach_recommends_follow_up_when_there_is_no_reply():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        lead = client.post("/api/leads", json={"name": "Andrei Furtos", "linkedin_url": "https://www.linkedin.com/in/andrei-coach-test/"}).json()
        response = client.post(f"/api/leads/{lead['id']}/coach", json={"latest_message": ""})
        assert response.status_code == 200
        assert response.json()["suggested_stage"] == "message_envoye"
        assert "courte relance" in response.json()["suggested_message"]


def test_coach_keeps_cv_referral_contact_to_reactivate():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        lead = client.post("/api/leads", json={"name": "Elodie Gabilly", "linkedin_url": "https://www.linkedin.com/in/elodie-coach-test/"}).json()
        response = client.post(f"/api/leads/{lead['id']}/coach", json={"latest_message": "Je n'ai pas de mission, mais transmettez-moi votre CV et gardons contact."})
        assert response.status_code == 200
        assert response.json()["suggested_stage"] == "a_reactiver"
        assert "transmets volontiers mon CV" in response.json()["suggested_message"]
