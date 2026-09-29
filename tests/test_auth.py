from io import BytesIO
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.config import settings
from app.database import engine
from app.models import LeadAction, LeadImportRun


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
        assert 'id="channel-strategy"' in response.text
        assert 'id="mission-channel"' in response.text
        assert 'id="lead-persona"' in response.text


def test_api_rejects_unauthenticated_requests():
    with TestClient(app) as client:
        response = client.get("/api/opportunities")
        assert response.status_code == 401


def test_linkedin_import_requires_key_and_deduplicates_urls():
    previous_key = settings.import_api_key
    settings.import_api_key = "test-import-key"
    unique_slug = f"import-webhook-test-{uuid4().hex}"
    payload = {
        "leads": [{
            "name": "Import LinkedIn Test",
            "headline": "Business Manager IT",
            "company": "ESN Test",
            "linkedin_url": f"https://www.linkedin.com/in/{unique_slug}/?trk=test",
            "connected_on": "2026-08-24",
        }]
    }
    try:
        with TestClient(app) as client:
            assert client.post("/api/imports/linkedin", json=payload).status_code == 401
            first = client.post("/api/imports/linkedin", json=payload, headers={"X-Import-Key": "test-import-key"})
            second = client.post("/api/imports/linkedin", json=payload, headers={"X-Import-Key": "test-import-key"})
            assert first.status_code == 200
            assert first.json()["created"] == 1
            assert first.json()["duplicates"] == 0
            assert second.json()["created"] == 0
            assert second.json()["duplicates"] == 1
            assert first.json()["total_leads"] >= 1
            assert first.json()["imported_at"]
            client.post("/login", data={"username": "admin", "password": "development-only"})
            latest = client.get("/api/imports/linkedin/latest")
            assert latest.status_code == 200
            assert latest.json()["examined"] == 1
            assert latest.json()["created"] == 0
            assert latest.json()["duplicates"] == 1
            with Session(engine) as db:
                assert db.scalar(select(LeadImportRun).order_by(LeadImportRun.id.desc())) is not None
    finally:
        settings.import_api_key = previous_key


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


def test_authenticated_user_can_archive_and_restore_an_opportunity():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        mission = client.post(
            "/api/opportunities",
            json={"title": "Mission à archiver", "company": "Client Test"},
        ).json()

        archived = client.patch(
            f"/api/opportunities/{mission['id']}/stage",
            json={"stage": "perdue"},
        )
        assert archived.status_code == 200
        assert archived.json()["stage"] == "perdue"

        restored = client.patch(
            f"/api/opportunities/{mission['id']}/stage",
            json={"stage": "nouvelle"},
        )
        assert restored.status_code == 200
        assert restored.json()["stage"] == "nouvelle"


def test_invalid_opportunity_stage_is_rejected():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        response = client.patch("/api/opportunities/1/stage", json={"stage": "nimporte_quoi"})
        assert response.status_code == 422


def test_invalid_lead_stage_is_rejected():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        response = client.patch("/api/leads/1/stage", json={"stage": "nimporte_quoi"})
        assert response.status_code == 422


def test_sales_agent_builds_a_human_validated_conversion_queue():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        lead = client.post("/api/leads", json={
            "name": "Camille Conversion",
            "headline": "Directrice CRM",
            "company": "Entreprise Test",
            "stage": "a_contacter",
            "linkedin_url": "https://www.linkedin.com/in/camille-conversion-agent-test/",
        }).json()

        briefing = client.post("/api/sales-agent/run", json={})
        assert briefing.status_code == 200
        action = next(item for item in briefing.json()["actions"] if item["lead_id"] == lead["id"])
        assert action["action_type"] == "premier_message"
        assert action["target_stage"] == "message_envoye"
        assert "agentification du CRM" in action["message"]

        confirmed = client.post(
            f"/api/sales-agent/actions/{action['id']}/confirm",
            json={"message": action["message"]},
        )
        assert confirmed.status_code == 200
        refreshed = next(item for item in client.get("/api/leads").json() if item["id"] == lead["id"])
        assert refreshed["stage"] == "message_envoye"


def test_sales_agent_can_snooze_without_contacting_the_lead():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        lead = client.post("/api/leads", json={
            "name": "Romain Report",
            "headline": "Business Manager IT",
            "company": "ESN Test",
            "stage": "a_contacter",
            "linkedin_url": "https://www.linkedin.com/in/romain-report-agent-test/",
        }).json()
        briefing = client.post("/api/sales-agent/run", json={}).json()
        action = next(item for item in briefing["actions"] if item["lead_id"] == lead["id"])
        snoozed = client.post(f"/api/sales-agent/actions/{action['id']}/snooze", json={"days": 3})
        assert snoozed.status_code == 200
        updated = next(item for item in snoozed.json()["actions"] if item["id"] == action["id"])
        assert updated["status"] == "snoozed"
        unchanged = next(item for item in client.get("/api/leads").json() if item["id"] == lead["id"])
        assert unchanged["stage"] == "a_contacter"


def test_automatic_outreach_only_exposes_and_records_high_score_first_contacts():
    previous_key = settings.import_api_key
    settings.import_api_key = "test-import-key"
    try:
        with TestClient(app) as client:
            client.post("/login", data={"username": "admin", "password": "development-only"})
            high = client.post("/api/leads", json={
                "name": "Amina Salesforce",
                "headline": "Business Manager Salesforce",
                "company": "ESN Salesforce",
                "stage": "a_contacter",
                "connected_on": "2026-09-16",
                "linkedin_url": "https://www.linkedin.com/in/amina-automatic-outreach-test/",
            }).json()
            low = client.post("/api/leads", json={
                "name": "Contact Généraliste",
                "headline": "Consultant",
                "company": "Entreprise",
                "stage": "a_contacter",
                "linkedin_url": "https://www.linkedin.com/in/contact-low-outreach-test/",
            }).json()
            ambiguous = client.post("/api/leads", json={
                "name": "H. D.",
                "headline": "Business Manager Salesforce",
                "company": "ESN Salesforce",
                "stage": "a_contacter",
                "connected_on": "2026-09-16",
                "linkedin_url": "https://www.linkedin.com/in/ambiguous-outreach-test/",
            }).json()

            assert client.get("/api/automation/outreach/candidates").status_code == 401
            response = client.get(
                "/api/automation/outreach/candidates",
                headers={"X-Import-Key": "test-import-key"},
            )
            assert response.status_code == 200
            ids = [item["lead_id"] for item in response.json()]
            assert high["id"] in ids
            assert low["id"] not in ids
            assert ambiguous["id"] not in ids
            candidate = next(item for item in response.json() if item["lead_id"] == high["id"])
            assert candidate["score"] > 80
            assert candidate["competencies"] == ["Salesforce"]

            unchanged = next(item for item in client.get("/api/leads").json() if item["id"] == high["id"])
            assert unchanged["stage"] == "a_contacter"
            sent = client.post(
                f"/api/automation/outreach/{high['id']}/sent",
                json={"message": candidate["message"]},
                headers={"X-Import-Key": "test-import-key"},
            )
            assert sent.status_code == 200
            assert sent.json()["stage"] == "message_envoye"
    finally:
        settings.import_api_key = previous_key


def test_existing_linkedin_conversation_is_reconciled_without_consuming_send_quota():
    previous_key = settings.import_api_key
    settings.import_api_key = "test-import-key"
    try:
        with TestClient(app) as client:
            client.post("/login", data={"username": "admin", "password": "development-only"})
            lead = client.post("/api/leads", json={
                "name": "Nadia Historique",
                "headline": "Business Manager Salesforce",
                "company": "ESN Salesforce",
                "stage": "a_contacter",
                "connected_on": "2026-09-16",
                "linkedin_url": "https://www.linkedin.com/in/nadia-existing-conversation-test/",
            }).json()
            reconciled = client.post(
                f"/api/automation/outreach/{lead['id']}/sent",
                json={"message": "Message LinkedIn antérieur confirmé", "already_existed": True},
                headers={"X-Import-Key": "test-import-key"},
            )
            assert reconciled.status_code == 200
            assert reconciled.json()["stage"] == "message_envoye"
            briefing = client.get(
                "/api/automation/outreach/candidates",
                headers={"X-Import-Key": "test-import-key"},
            )
            assert all(item["lead_id"] != lead["id"] for item in briefing.json())
    finally:
        settings.import_api_key = previous_key


def test_post_waalaxy_sequence_qualifies_then_nurtures_a_high_score_prospect():
    previous_key = settings.import_api_key
    settings.import_api_key = "test-import-key"
    try:
        with TestClient(app) as client:
            client.post("/login", data={"username": "admin", "password": "development-only"})
            lead = client.post("/api/leads", json={
                "name": "Romain Pipeline",
                "headline": "Business Manager Salesforce",
                "company": "ESN Salesforce",
                "stage": "a_contacter",
                "connected_on": "2026-09-01",
                "linkedin_url": "https://www.linkedin.com/in/romain-pipeline-followup-test/",
            }).json()
            client.post(
                f"/api/automation/outreach/{lead['id']}/sent",
                json={"message": "Message Waalaxy confirmé", "already_existed": True},
                headers={"X-Import-Key": "test-import-key"},
            )
            with Session(engine) as db:
                first_contact = db.scalar(select(LeadAction).where(
                    LeadAction.lead_id == lead["id"],
                    LeadAction.action_type == "existing_first_contact",
                ))
                first_contact.completed_at = datetime.utcnow() - timedelta(days=4)
                db.commit()

            candidates = client.get(
                "/api/automation/follow-ups/candidates",
                headers={"X-Import-Key": "test-import-key"},
            )
            assert candidates.status_code == 200
            candidate = next(item for item in candidates.json() if item["lead_id"] == lead["id"])
            assert candidate["sequence_step"] == "qualification_j3"
            assert "dans votre pipe" in candidate["message"]
            assert "architecture, la gouvernance" in candidate["message"]

            sent = client.post(
                f"/api/automation/follow-ups/{lead['id']}/sent",
                json={"sequence_step": "qualification_j3", "message": candidate["message"]},
                headers={"X-Import-Key": "test-import-key"},
            )
            assert sent.status_code == 200
            assert sent.json()["stage"] == "message_envoye"
    finally:
        settings.import_api_key = previous_key


def test_inbound_reply_stops_automatic_no_response_sequence():
    previous_key = settings.import_api_key
    settings.import_api_key = "test-import-key"
    try:
        with TestClient(app) as client:
            client.post("/login", data={"username": "admin", "password": "development-only"})
            lead = client.post("/api/leads", json={
                "name": "Sophie Decision",
                "headline": "Directrice CRM Salesforce",
                "company": "Grand Groupe",
                "stage": "a_contacter",
                "connected_on": "2026-09-01",
                "linkedin_url": "https://www.linkedin.com/in/sophie-response-followup-test/",
            }).json()
            client.post(
                f"/api/automation/outreach/{lead['id']}/sent",
                json={"message": "Message Waalaxy confirmé", "already_existed": True},
                headers={"X-Import-Key": "test-import-key"},
            )
            response = client.post(
                f"/api/automation/follow-ups/{lead['id']}/response",
                json={"message": "Nous aurons peut-être un besoin Salesforce le mois prochain."},
                headers={"X-Import-Key": "test-import-key"},
            )
            assert response.status_code == 200
            assert response.json()["stage"] == "echange_en_cours"
            candidates = client.get(
                "/api/automation/follow-ups/candidates",
                headers={"X-Import-Key": "test-import-key"},
            ).json()
            assert all(item["lead_id"] != lead["id"] for item in candidates)
    finally:
        settings.import_api_key = previous_key


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
        lead = client.post("/api/leads", json={"name": "Antoine Driguet", "headline": "Business Manager IT", "company": "Profila France", "stage": "a_contacter", "linkedin_url": "https://www.linkedin.com/in/antoine-coach-test/"}).json()
        response = client.post(f"/api/leads/{lead['id']}/coach", json={"latest_message": ""})
        assert response.status_code == 200
        assert response.json()["suggested_stage"] == "message_envoye"
        assert "merci d’avoir accepté ma demande de connexion" in response.json()["suggested_message"]
        assert "responsable de practice" in response.json()["suggested_message"]
        assert "CV" not in response.json()["suggested_message"]
        assert "relance" not in response.json()["suggested_message"]


def test_coach_adapts_post_acceptance_message_for_crm_decision_maker():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        lead = client.post("/api/leads", json={"name": "Sophie Martin", "headline": "Directrice CRM", "company": "Grand Groupe", "stage": "a_contacter", "linkedin_url": "https://www.linkedin.com/in/sophie-decision-test/"}).json()
        response = client.post(f"/api/leads/{lead['id']}/coach", json={"latest_message": ""})
        assert response.status_code == 200
        assert "agentification du CRM" in response.json()["suggested_message"]
        assert "résultats mesurables" in response.json()["suggested_message"]
        assert "CV" not in response.json()["suggested_message"]


def test_coach_adapts_post_acceptance_message_for_salesforce_partner():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        lead = client.post("/api/leads", json={"name": "Malek Ben Miled", "headline": "Architecte Salesforce indépendant", "stage": "a_contacter", "linkedin_url": "https://www.linkedin.com/in/malek-partner-test/"}).json()
        response = client.post(f"/api/leads/{lead['id']}/coach", json={"latest_message": ""})
        assert response.status_code == 200
        assert "recommander mutuellement" in response.json()["suggested_message"]


def test_coach_detects_a_scheduled_meeting():
    with TestClient(app) as client:
        client.post("/login", data={"username": "admin", "password": "development-only"})
        lead = client.post("/api/leads", json={"name": "Riad Gulabkhan", "headline": "Business Manager", "linkedin_url": "https://www.linkedin.com/in/riad-meeting-test/"}).json()
        response = client.post(f"/api/leads/{lead['id']}/coach", json={"latest_message": "Je vous propose un échange via Teams demain."})
        assert response.status_code == 200
        assert response.json()["suggested_stage"] == "rendez_vous_planifie"


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
