from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app.main import app


def sample_docx() -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Architecte Salesforce senior</w:t></w:r></w:p></w:body></w:document>')
    return stream.getvalue()


def test_health_is_public():
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200


def test_home_redirects_to_login():
    with TestClient(app) as client:
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_authenticated_home_contains_lead_kanban():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        response = client.get("/")
        assert response.status_code == 200
        assert 'id="lead-kanban"' in response.text
        assert 'data-lead-layout="kanban"' in response.text


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


def test_profile_stores_behavioral_insights_separately_from_cv():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        current = client.get("/api/profile").json()
        current.update({
            "soft_skill_profile": "Autonome, calme sous pression et persévérant",
            "work_preferences": "Culture collaborative, innovante et responsabilisante",
            "development_points": "Décision parfois prudente et tendance à approfondir",
        })
        response = client.put("/api/profile", json=current)
        assert response.status_code == 200
        assert response.json()["soft_skill_profile"].startswith("Autonome")
        assert response.json()["work_preferences"].startswith("Culture collaborative")


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


def test_opportunity_coach_prepares_direct_application_message():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        mission = client.post("/api/opportunities", json={"title": "Expert Salesforce", "company": "NaxoTech", "description": "Architecture Salesforce et gouvernance", "source_url": "https://www.linkedin.com/company/naxotech/posts/"}).json()
        response = client.post(f"/api/opportunities/{mission['id']}/coach")
        assert response.status_code == 200
        assert response.json()["suggested_stage"] == "contact"
        message = response.json()["suggested_message"]
        assert "candidature à la mission « Expert Salesforce »" in message
        assert "plus de 10 ans sur Salesforce" in message
        assert "40 %" in message
        assert "65 %" in message
        assert "CV ciblé" in message
        assert "échange de 15 minutes" in message


def test_coach_recommends_first_message_for_lead_to_contact():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        lead = client.post("/api/leads", json={"name": "Antoine Driguet", "company": "Profila France", "stage": "a_contacter", "linkedin_url": "https://www.linkedin.com/in/antoine-coach-test/"}).json()
        response = client.post(f"/api/leads/{lead['id']}/coach", json={"latest_message": ""})
        assert response.status_code == 200
        assert response.json()["suggested_stage"] == "message_envoye"
        assert "merci d’avoir accepté ma demande de connexion" in response.json()["suggested_message"]
        assert "TotalEnergies" in response.json()["suggested_message"]
        assert "je suis Architecte CRM/Solution senior" not in response.json()["suggested_message"]
        assert "relance" not in response.json()["suggested_message"]


def test_coach_recommends_follow_up_after_message_was_sent():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        lead = client.post("/api/leads", json={"name": "Andrei Furtos", "stage": "message_envoye", "linkedin_url": "https://www.linkedin.com/in/andrei-follow-up-test/"}).json()
        response = client.post(f"/api/leads/{lead['id']}/coach", json={"latest_message": ""})
        assert response.status_code == 200
        assert "courte relance" in response.json()["suggested_message"]


def test_coach_keeps_cv_referral_contact_to_reactivate():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        lead = client.post("/api/leads", json={"name": "Elodie Gabilly", "linkedin_url": "https://www.linkedin.com/in/elodie-coach-test/"}).json()
        response = client.post(f"/api/leads/{lead['id']}/coach", json={"latest_message": "Je n'ai pas de mission, mais transmettez-moi votre CV et gardons contact."})
        assert response.status_code == 200
        assert response.json()["suggested_stage"] == "a_reactiver"
        assert "transmets volontiers mon CV" in response.json()["suggested_message"]


def test_authenticated_user_can_store_and_download_base_cv():
    content = sample_docx()
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        uploaded = client.post("/api/profile/cv", files={"file": ("CV Boubacar.docx", content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        assert uploaded.status_code == 200
        assert uploaded.json()["filename"] == "CV Boubacar.docx"
        assert client.get("/api/profile").json()["cv_text"] == "Architecte Salesforce senior"
        downloaded = client.get("/api/profile/cv/word")
        assert downloaded.status_code == 200
        assert downloaded.content == content


def test_pdf_is_generated_from_stored_word(monkeypatch):
    def fake_run(args, **_kwargs):
        output_dir = args[args.index("--outdir") + 1]
        from pathlib import Path
        Path(output_dir, "cv.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("app.main.shutil.which", lambda _name: "/usr/bin/libreoffice")
    monkeypatch.setattr("app.main.subprocess.run", fake_run)
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        client.post("/api/profile/cv", files={"file": ("CV Boubacar.docx", sample_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        response = client.get("/api/profile/cv/pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")
