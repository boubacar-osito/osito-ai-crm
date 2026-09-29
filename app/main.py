from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from hashlib import sha256
import hmac
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import zipfile
from xml.etree import ElementTree

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth, OAuthError
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, engine, get_db
from .models import CandidateDocument, CandidateProfile, Contact, Lead, LeadAction, LeadImportRun, Opportunity
from .schemas import ATSRequest, ATSResult, AutomatedFollowupCandidate, AutomatedFollowupSent, AutomatedLeadResponse, AutomatedOutreachCandidate, AutomatedOutreachSent, ContactCreate, LeadCoachRequest, LeadCoachResult, LeadCreate, LeadImportBatch, LeadImportResult, LeadImportStatus, LeadOut, LeadStageUpdate, OpportunityCoachResult, OpportunityCreate, OpportunityOut, OpportunityStageUpdate, ProfileOut, ProfilePayload, SalesAgentActionOut, SalesAgentBriefing, SalesAgentConfirm, SalesAgentSnooze
from .scoring import build_ats_result, score_lead, score_opportunity


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    existing_columns = {column["name"] for column in inspect(engine).get_columns("candidate_profiles")}
    profile_insight_columns = {
        "soft_skill_profile": "TEXT NOT NULL DEFAULT ''",
        "work_preferences": "TEXT NOT NULL DEFAULT ''",
        "development_points": "TEXT NOT NULL DEFAULT ''",
    }
    with engine.begin() as connection:
        for column, definition in profile_insight_columns.items():
            if column not in existing_columns:
                connection.execute(text(f"ALTER TABLE candidate_profiles ADD COLUMN {column} {definition}"))
    with Session(engine) as db:
        if not db.scalar(select(CandidateProfile).limit(1)):
            db.add(CandidateProfile())
            db.commit()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
oauth = OAuth()
if settings.google_client_id and settings.google_client_secret:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


@app.middleware("http")
async def require_login(request: Request, call_next):
    public_paths = {"/login", "/auth/google", "/auth/google/callback", "/api/health"}
    if request.url.path == "/api/imports/linkedin" or request.url.path.startswith("/api/automation/"):
        supplied_key = request.headers.get("X-Import-Key", "")
        configured_key = settings.import_api_key
        if not configured_key or not hmac.compare_digest(supplied_key, configured_key):
            return JSONResponse({"detail": "Clé d'import invalide"}, status_code=401)
        return await call_next(request)
    if request.url.path in public_paths or request.url.path.startswith("/static/"):
        return await call_next(request)
    if not request.session.get("authenticated"):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Authentification requise"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


# Added after the authentication middleware so session data is available to it.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    https_only=settings.app_env == "production",
    same_site="lax",
    max_age=60 * 60 * 12,
)


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(static_dir / "login.html")


@app.get("/auth/google", include_in_schema=False)
async def google_login(request: Request):
    if not settings.google_client_id or not settings.google_client_secret:
        return RedirectResponse("/login?oauth=unavailable", status_code=303)
    redirect_uri = request.url_for("google_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google/callback", include_in_schema=False)
async def google_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        return RedirectResponse("/login?oauth=failed", status_code=303)
    user = token.get("userinfo") or {}
    email = str(user.get("email", "")).lower()
    verified = bool(user.get("email_verified"))
    allowed = settings.google_allowed_email.lower()
    if not verified or not allowed or not hmac.compare_digest(email, allowed):
        request.session.clear()
        return RedirectResponse("/login?oauth=forbidden", status_code=303)
    request.session.clear()
    request.session.update({"authenticated": True, "email": email, "name": user.get("name", "")})
    return RedirectResponse("/", status_code=303)


@app.post("/login", include_in_schema=False)
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if not settings.local_login_enabled:
        return RedirectResponse("/login?local=disabled", status_code=303)
    valid_user = hmac.compare_digest(username, settings.app_username)
    valid_password = hmac.compare_digest(password, settings.app_password)
    if not (valid_user and valid_password):
        return RedirectResponse("/login?error=1", status_code=303)
    request.session.clear()
    request.session["authenticated"] = True
    return RedirectResponse("/", status_code=303)


@app.post("/logout", include_in_schema=False)
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(static_dir / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/profile", response_model=ProfileOut)
def get_profile(db: Session = Depends(get_db)):
    return db.scalar(select(CandidateProfile).limit(1))


@app.put("/api/profile", response_model=ProfileOut)
def save_profile(payload: ProfilePayload, db: Session = Depends(get_db)):
    profile = db.scalar(select(CandidateProfile).limit(1)) or CandidateProfile()
    for key, value in payload.model_dump().items():
        setattr(profile, key, value)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    for opportunity in db.scalars(select(Opportunity)).all():
        opportunity.score, opportunity.score_details = score_opportunity(opportunity, profile)
    db.commit()
    return profile


def extract_docx_text(content: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise HTTPException(400, "Le fichier fourni n'est pas un document Word .docx valide") from exc
    root = ElementTree.fromstring(xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t")).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


@app.get("/api/profile/cv")
def get_base_cv(db: Session = Depends(get_db)):
    document = db.scalar(select(CandidateDocument).order_by(CandidateDocument.uploaded_at.desc()).limit(1))
    if not document:
        return {"available": False}
    return {"available": True, "filename": document.filename, "size": len(document.content), "uploaded_at": document.uploaded_at}


@app.post("/api/profile/cv")
async def upload_base_cv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    filename = Path(file.filename or "cv.docx").name
    if not filename.lower().endswith(".docx"):
        raise HTTPException(400, "Choisis un document Word au format .docx")
    content = await file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, "Le CV dépasse la taille maximale de 5 Mo")
    cv_text = extract_docx_text(content)
    if not cv_text:
        raise HTTPException(400, "Le document Word ne contient aucun texte exploitable")
    db.query(CandidateDocument).delete()
    document = CandidateDocument(filename=filename, content_type=file.content_type or "application/vnd.openxmlformats-officedocument.wordprocessingml.document", sha256=sha256(content).hexdigest(), content=content)
    db.add(document)
    profile = db.scalar(select(CandidateProfile).limit(1)) or CandidateProfile()
    profile.cv_text = cv_text
    db.add(profile)
    db.commit()
    db.refresh(document)
    return {"available": True, "filename": document.filename, "size": len(document.content), "uploaded_at": document.uploaded_at}


def current_document(db: Session) -> CandidateDocument:
    document = db.scalar(select(CandidateDocument).order_by(CandidateDocument.uploaded_at.desc()).limit(1))
    if not document:
        raise HTTPException(404, "Ajoute d'abord un CV Word de référence")
    return document


@app.get("/api/profile/cv/word")
def download_base_cv(db: Session = Depends(get_db)):
    document = current_document(db)
    return Response(content=document.content, media_type=document.content_type, headers={"Content-Disposition": f'attachment; filename="{document.filename}"'})


@app.get("/api/profile/cv/pdf")
def download_base_cv_pdf(db: Session = Depends(get_db)):
    document = current_document(db)
    converter = shutil.which("libreoffice") or shutil.which("soffice")
    if not converter:
        raise HTTPException(503, "Le convertisseur PDF n'est pas disponible sur le serveur")
    with TemporaryDirectory(prefix="missionflow-cv-") as directory:
        source = Path(directory) / "cv.docx"
        source.write_bytes(document.content)
        profile_uri = (Path(directory) / "libreoffice-profile").as_uri()
        result = subprocess.run(
            [converter, f"-env:UserInstallation={profile_uri}", "--headless", "--convert-to", "pdf", "--outdir", directory, str(source)],
            capture_output=True,
            timeout=45,
            check=False,
            env={**os.environ, "HOME": directory},
        )
        pdf_path = Path(directory) / "cv.pdf"
        if result.returncode or not pdf_path.exists():
            raise HTTPException(500, "La génération du PDF a échoué")
        pdf = pdf_path.read_bytes()
    output_name = f"{Path(document.filename).stem}.pdf"
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{output_name}"'})


@app.post("/api/contacts")
def create_contact(payload: ContactCreate, db: Session = Depends(get_db)):
    contact = Contact(**payload.model_dump())
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


@app.get("/api/leads", response_model=list[LeadOut])
def list_leads(db: Session = Depends(get_db)):
    return db.scalars(select(Lead).order_by(Lead.score.desc(), Lead.connected_on.desc())).all()


@app.post("/api/leads", response_model=LeadOut)
def create_lead(payload: LeadCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(Lead).where(Lead.linkedin_url == payload.linkedin_url))
    lead = existing or Lead()
    for key, value in payload.model_dump().items():
        setattr(lead, key, value)
    lead.score, lead.score_details = score_lead(lead)
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def normalize_linkedin_url(value: str) -> str:
    return value.strip().split("?")[0].rstrip("/").lower()


def lead_persona(lead: Lead) -> str:
    text = f"{lead.headline} {lead.company}".lower()
    introducer_terms = (
        "business manager", "ingénieur d'affaire", "ingénieur d’affaire", "recruteur",
        "recruteuse", "recrutement", "talent acquisition", "talent specialist",
        "account manager", "staffing", "commercial", "portage", "esn", "cabinet",
    )
    decision_terms = (
        "dsi", "cio", "head of", "directeur crm", "directrice crm", "responsable crm",
        "salesforce manager", "product owner salesforce", "customer experience",
        "transformation", "digital director", "chief information",
    )
    partner_terms = (
        "architecte salesforce", "salesforce architect", "consultant salesforce",
        "expert salesforce", "freelance salesforce", "founder", "fondateur", "fondatrice",
    )
    if any(term in text for term in introducer_terms):
        return "apporteur"
    if any(term in text for term in decision_terms):
        return "decideur"
    if any(term in text for term in partner_terms):
        return "partenaire"
    return "contact"


def post_acceptance_message(lead: Lead, first_name: str) -> tuple[str, str, str]:
    persona = lead_persona(lead)
    company = f" chez {lead.company}" if lead.company else ""
    if persona == "apporteur":
        return (
            "apporteur de missions",
            "Vérifier s'il porte un besoin actif ou peut vous orienter vers le bon responsable de practice.",
            f"Bonjour {first_name}, merci d’avoir accepté ma demande de connexion. "
            "J’interviens sur des missions d’architecture, de delivery et de gouvernance Salesforce, "
            "notamment sur des contextes multi-org, Sales/Service Cloud, Data et MuleSoft. "
            f"Au regard de votre activité{company}, avez-vous actuellement ou prochainement un besoin "
            "sur lequel ce positionnement pourrait être utile, ou un responsable de practice vers qui m’orienter ?",
        )
    if persona == "decideur":
        return (
            "décideur CRM/SI",
            "Ouvrir un échange entre pairs sur une priorité mesurable, sans proposer immédiatement votre CV.",
            f"Bonjour {first_name}, merci d’avoir accepté ma demande de connexion. "
            "J’accompagne des organisations sur l’harmonisation de leurs usages Salesforce et sur la traduction "
            "des orientations CRM en résultats mesurables : adoption, conversion, renouvellement et qualité de service. "
            f"Dans votre contexte{company}, quel sujet est aujourd’hui prioritaire : gouvernance multi-org, "
            "optimisation des parcours ou agentification du CRM ? Je serais ravi d’échanger 15 minutes sur vos enjeux.",
        )
    if persona == "partenaire":
        return (
            "partenaire Salesforce potentiel",
            "Tester une logique de complémentarité et de recommandations croisées.",
            f"Bonjour {first_name}, merci d’avoir accepté ma demande de connexion. "
            "Nos expertises Salesforce semblent complémentaires. Je développe un réseau resserré de partenaires "
            "pour partager des opportunités, compléter des équipes et répondre ensemble à certains besoins clients. "
            "Seriez-vous disponible pour un échange de 15 minutes afin de voir dans quels cas nous pourrions nous recommander mutuellement ?",
        )
    return (
        "contact à qualifier",
        "Comprendre son rôle dans l'écosystème et obtenir une orientation précise.",
        f"Bonjour {first_name}, merci d’avoir accepté ma demande de connexion. "
        "Je suis spécialisé en architecture, delivery et gouvernance Salesforce pour des transformations CRM complexes. "
        f"Votre activité{company} a retenu mon attention : intervenez-vous directement sur ces sujets, "
        "ou pourriez-vous m’indiquer la personne la plus pertinente avec qui échanger ?",
    )


FOLLOWUP_ACTION_TYPES = {
    "qualification_j3": "auto_followup_qualification_j3",
    "valeur_j10": "auto_followup_valeur_j10",
    "reactivation_j30": "auto_followup_reactivation_j30",
}


def followup_message(lead: Lead, sequence_step: str) -> tuple[str, str]:
    """Build a short post-Waalaxy message adapted to the contact's role."""
    first_name = lead.name.split()[0] if lead.name else ""
    persona = lead_persona(lead)
    company = f" chez {lead.company}" if lead.company else ""
    if sequence_step == "qualification_j3":
        messages = {
            "apporteur": (
                "Qualifier le prospect et détecter une opportunité disponible dans son pipe.",
                f"Bonjour {first_name}, pour mieux comprendre les sujets que vous suivez{company}, "
                "avez-vous actuellement dans votre pipe une mission CRM ou Salesforce ouverte, ou susceptible "
                "de démarrer dans les prochaines semaines ? Je cible surtout l’architecture, la gouvernance et "
                "le delivery sur des environnements complexes."
            ),
            "decideur": (
                "Identifier une priorité CRM/Salesforce et l'éventuel recours à un renfort externe.",
                f"Bonjour {first_name}, parmi vos priorités CRM ou Salesforce{company}, avez-vous un chantier "
                "prévu dans les trois à six prochains mois sur lequel un renfort externe senior pourrait être utile : "
                "architecture, gouvernance, adoption ou optimisation des parcours ?"
            ),
            "partenaire": (
                "Détecter une complémentarité immédiate ou une opportunité à traiter ensemble.",
                f"Bonjour {first_name}, pour rendre notre mise en relation concrète, avez-vous actuellement un projet "
                "Salesforce nécessitant un renfort senior ou une expertise complémentaire ? De mon côté, je peux "
                "intervenir sur l’architecture, la gouvernance et le delivery CRM."
            ),
            "contact": (
                "Confirmer le rôle du contact et obtenir l'interlocuteur ou le besoin pertinent.",
                f"Bonjour {first_name}, afin de vous solliciter uniquement sur les sujets pertinents, intervenez-vous "
                "sur les besoins CRM/Salesforce de votre organisation ? Si oui, avez-vous un chantier ouvert ou à venir ; "
                "sinon, qui serait la bonne personne avec qui échanger ?"
            ),
        }
        return messages[persona]
    if sequence_step == "valeur_j10":
        messages = {
            "apporteur": (
                "Créer un repère mémorable pour être rappelé dès qu'un besoin entre dans le pipe.",
                f"Bonjour {first_name}, un repère simple pour vos prochains besoins : j’interviens lorsque le client "
                "doit cadrer une trajectoire Salesforce, sécuriser un delivery complexe ou harmoniser plusieurs orgs. "
                "Quels critères vous seraient les plus utiles pour me positionner rapidement lorsqu’une mission de ce type apparaît ?"
            ),
            "decideur": (
                "Apporter un angle concret et faire émerger un enjeu prioritaire.",
                f"Bonjour {first_name}, sur les transformations CRM, trois signaux déclenchent souvent mon intervention : "
                "des usages hétérogènes, une dette qui ralentit le delivery ou des résultats métier difficiles à mesurer. "
                "L’un de ces sujets est-il présent dans votre contexte cette année ?"
            ),
            "partenaire": (
                "Rendre la recommandation mutuelle simple et actionnable.",
                f"Bonjour {first_name}, pour faciliter une recommandation future, mes terrains les plus forts sont les "
                "programmes Salesforce complexes, la gouvernance multi-org et l’alignement métier–SI. Quels types de "
                "missions ou de compétences souhaitez-vous, de votre côté, que je garde en tête pour vous ?"
            ),
            "contact": (
                "Obtenir une orientation sans répéter la présentation initiale.",
                f"Bonjour {first_name}, je précise mon angle pour faciliter l’orientation : architecture et gouvernance "
                "Salesforce, cadrage CRM et sécurisation du delivery. Est-ce un sujet suivi par votre équipe, ou puis-je "
                "contacter de votre part l’interlocuteur qui le porte ?"
            ),
        }
        return messages[persona]
    messages = {
        "apporteur": (
            "Revenir dans le radar au moment où de nouveaux besoins peuvent entrer dans le pipe.",
            f"Bonjour {first_name}, petit point de disponibilité : je reste mobilisable pour une mission senior CRM/Salesforce. "
            "Un besoin d’architecture, de gouvernance ou de delivery est-il entré récemment dans votre pipe ? Si le timing "
            "n’est pas encore défini, je peux aussi vous donner mes critères de disponibilité pour le prochain trimestre."
        ),
        "decideur": (
            "Réactiver la relation avec une question liée au calendrier de transformation.",
            f"Bonjour {first_name}, je me permets de reprendre contact : vos priorités CRM/Salesforce pour le prochain "
            "trimestre sont-elles désormais arrêtées ? Je reste disponible si un regard senior externe peut accélérer "
            "le cadrage ou sécuriser l’exécution."
        ),
        "partenaire": (
            "Maintenir la relation et rouvrir la possibilité d'une coopération.",
            f"Bonjour {first_name}, je reprends brièvement contact pour le prochain trimestre. Avez-vous identifié un besoin "
            "Salesforce sur lequel une complémentarité serait utile ? Je garde également votre positionnement en tête "
            "pour les opportunités que je pourrais croiser."
        ),
        "contact": (
            "Vérifier si le contexte a évolué et obtenir une orientation.",
            f"Bonjour {first_name}, je me permets un dernier point : un besoin CRM/Salesforce s’est-il précisé récemment "
            "dans votre organisation ? Dans le cas contraire, je serais reconnaissant si vous pouviez m’indiquer le bon interlocuteur."
        ),
    }
    return messages[persona]


def automated_followup_plan(lead: Lead, db: Session) -> dict | None:
    """Return the next due step after the Waalaxy introduction, or None."""
    if lead.stage != "message_envoye" or lead.score <= 80:
        return None
    actions = db.scalars(
        select(LeadAction)
        .where(LeadAction.lead_id == lead.id, LeadAction.status == "completed")
        .order_by(LeadAction.completed_at.desc(), LeadAction.id.desc())
    ).all()
    action_by_type = {action.action_type: action for action in actions}
    first_contact = next((
        action for action in actions
        if action.action_type in ("auto_first_contact", "existing_first_contact", "premier_message")
    ), None)
    # Candidate-list reads must never postpone a legacy follow-up. When no
    # completed first-contact audit exists, use the immutable creation date
    # rather than ``updated_at`` (which also changes when scores are refreshed).
    anchor = (first_contact.completed_at if first_contact and first_contact.completed_at else lead.created_at)
    sequence = (
        ("qualification_j3", 3, None),
        ("valeur_j10", 7, "qualification_j3"),
        ("reactivation_j30", 20, "valeur_j10"),
    )
    for step, wait_days, previous_step in sequence:
        action_type = FOLLOWUP_ACTION_TYPES[step]
        if action_type in action_by_type:
            continue
        if previous_step:
            previous = action_by_type.get(FOLLOWUP_ACTION_TYPES[previous_step])
            if not previous or not previous.completed_at:
                return None
            due_at = previous.completed_at + timedelta(days=wait_days)
        else:
            due_at = anchor + timedelta(days=wait_days)
        objective, message = followup_message(lead, step)
        return {"sequence_step": step, "objective": objective, "message": message, "due_at": due_at}
    return None


@app.post("/api/imports/linkedin", response_model=LeadImportResult)
def import_linkedin_leads(payload: LeadImportBatch, db: Session = Depends(get_db)):
    existing = {
        normalize_linkedin_url(url)
        for url in db.scalars(select(Lead.linkedin_url)).all()
        if url
    }
    created_ids = []
    duplicates = 0
    for item in payload.leads:
        normalized_url = normalize_linkedin_url(item.linkedin_url)
        if normalized_url in existing:
            duplicates += 1
            continue
        lead = Lead(**item.model_dump())
        lead.linkedin_url = item.linkedin_url.strip().split("?")[0].rstrip("/") + "/"
        lead.stage = "a_contacter"
        lead.score, lead.score_details = score_lead(lead)
        db.add(lead)
        db.flush()
        created_ids.append(lead.id)
        existing.add(normalized_url)
    imported_at = datetime.utcnow()
    total_leads = db.scalar(select(func.count(Lead.id))) or 0
    db.add(LeadImportRun(
        source=payload.leads[0].source or "LinkedIn — import automatique",
        examined=len(payload.leads),
        created=len(created_ids),
        duplicates=duplicates,
        total_leads=total_leads,
        imported_at=imported_at,
    ))
    db.commit()
    return LeadImportResult(
        examined=len(payload.leads),
        created=len(created_ids),
        duplicates=duplicates,
        lead_ids=created_ids,
        total_leads=total_leads,
        imported_at=imported_at,
    )


@app.get("/api/imports/linkedin/latest", response_model=LeadImportStatus | None)
def latest_linkedin_import(db: Session = Depends(get_db)):
    return db.scalar(select(LeadImportRun).order_by(LeadImportRun.imported_at.desc(), LeadImportRun.id.desc()).limit(1))


@app.patch("/api/leads/{lead_id}/stage", response_model=LeadOut)
def update_lead_stage(lead_id: int, payload: LeadStageUpdate, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Piste introuvable")
    lead.stage = payload.stage
    lead.score, lead.score_details = score_lead(lead)
    db.commit()
    db.refresh(lead)
    return lead


@app.post("/api/leads/{lead_id}/coach", response_model=LeadCoachResult)
def coach_lead(lead_id: int, payload: LeadCoachRequest, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Piste introuvable")
    first_name = lead.name.split()[0]
    message = payload.latest_message.strip()
    normalized = message.lower()

    if message and any(term in normalized for term in ("teams", "rendez-vous", "rendez vous", "créneau", "creneau", "disponible demain", "appel demain")):
        return LeadCoachResult(
            situation="Le contact propose ou confirme un échange.",
            objective="Sécuriser le rendez-vous et préparer trois questions de qualification.",
            next_action="Confirmer le créneau, puis classer la piste en « Rendez-vous planifié ».",
            suggested_stage="rendez_vous_planifie",
            suggested_message=f"Bonjour {first_name}, merci pour votre retour. Avec plaisir pour cet échange. Le créneau proposé me convient ; vous pouvez m’envoyer l’invitation Teams. Je préparerai une présentation synthétique de mon positionnement ainsi que quelques questions sur vos besoins Salesforce actuels et à venir. À très bientôt.",
        )

    if message and any(term in normalized for term in ("mettre en relation", "mets en relation", "mettrai en relation", "transmettre vos coordonnées", "transmettre votre profil")):
        return LeadCoachResult(
            situation="Le contact accepte de vous recommander ou de vous mettre en relation.",
            objective="Faciliter l'introduction avec une formulation courte et transférable.",
            next_action="Remercier, envoyer votre résumé en trois lignes et classer la piste en « Mise en relation ».",
            suggested_stage="mise_en_relation",
            suggested_message=f"Bonjour {first_name}, merci beaucoup pour votre proposition. Pour faciliter la mise en relation : Boubacar Diaby, Architecte CRM/Solution senior spécialisé Salesforce, intervient sur l’architecture, le delivery, la gouvernance multi-org et l’optimisation mesurable des parcours CRM. Je suis actuellement disponible en freelance. Je vous transmets volontiers mon CV si cela peut être utile à votre contact.",
        )

    if message and any(term in normalized for term in ("pas de mission", "aucune mission", "n'ai pas de mission", "n’ai pas de mission")):
        if any(term in normalized for term in ("cv", "gardons contact", "mettrez en relation", "mettrai en relation")):
            return LeadCoachResult(
                situation="Pas de besoin immédiat, mais le contact accepte de devenir prescripteur.",
                objective="Envoyer le CV et obtenir l'autorisation de revenir vers ce contact.",
                next_action="Joindre le CV ciblé Architecte CRM/Salesforce, puis programmer une relance dans 4 à 6 semaines.",
                suggested_stage="a_reactiver",
                suggested_message=f"Bonjour {first_name}, merci pour votre retour et pour votre proposition. Je vous transmets volontiers mon CV. Mon positionnement : Architecte CRM/Solution senior, spécialisé Salesforce et transformation SI, disponible en freelance. Si l’un de vos partenaires recherche ce type de profil, je serai ravi d’échanger rapidement avec lui. Je garde également vos coordonnées pour le portage salarial et reviendrai vers vous si le sujet se concrétise. Belle journée !",
            )
        return LeadCoachResult(
            situation="Le contact n'a pas de mission disponible actuellement.", objective="Rester dans son radar sans insister.",
            next_action="Remercier et programmer une relance dans 4 à 6 semaines.", suggested_stage="a_reactiver",
            suggested_message=f"Bonjour {first_name}, merci pour votre transparence. Je reste disponible pour toute mission d’architecture CRM/Salesforce ou de transformation SI qui pourrait se présenter dans votre réseau. Gardons le contact et belle journée !",
        )
    if message:
        return LeadCoachResult(
            situation="Le contact a répondu : la piste est désormais qualifiée.", objective="Identifier un besoin, un calendrier ou une mise en relation concrète.",
            next_action="Répondre avec une question précise et proposer un échange de 15 minutes ; relancer à J+3 si nécessaire.", suggested_stage="qualifiee",
            suggested_message=f"Bonjour {first_name}, merci pour votre retour. Parmi vos sujets actuels ou à venir, identifiez-vous un besoin autour de l’architecture Salesforce, de la gouvernance CRM ou de l’optimisation des parcours ? Si oui, je vous propose un échange de 15 minutes afin de qualifier rapidement le contexte, le calendrier et les interlocuteurs concernés.",
        )

    if lead.stage in ("nouvelle", "a_contacter"):
        persona_label, objective, suggested_message = post_acceptance_message(lead, first_name)
        situation = f"La connexion est acceptée ; le contact est identifié comme {persona_label}."
        next_action = "Personnaliser puis envoyer ce message. Sans réponse, relancer à J+3 et apporter un cas concret à J+8."
        return LeadCoachResult(
            situation=situation,
            objective=objective,
            next_action=next_action,
            suggested_stage="message_envoye",
            suggested_message=suggested_message,
        )

    if lead.stage in ("a_reactiver", "a_nourrir"):
        return LeadCoachResult(
            situation="Le contact est connu, mais la conversation doit être réactivée.",
            objective="Revenir dans son radar avec une disponibilité et un positionnement précis.",
            next_action="Envoyer une reprise de contact contextualisée, sans présenter le message comme une première approche.",
            suggested_stage="message_envoye",
            suggested_message=f"Bonjour {first_name}, je me permets de reprendre contact. Je suis actuellement disponible pour une mission freelance d’architecture CRM/Salesforce ou de transformation SI. Avez-vous identifié récemment un besoin correspondant dans votre réseau ou auprès de vos clients ? Je peux vous transmettre mon CV actualisé et mes disponibilités.",
        )

    if lead.stage == "echange_en_cours":
        return LeadCoachResult(
            situation="La conversation est engagée, mais sa dernière réponse manque pour préparer une réponse pertinente.",
            objective="Répondre au contenu réel de l'échange.",
            next_action="Coller le dernier message reçu dans le champ ci-dessus, puis relancer l'analyse.",
            suggested_stage="echange_en_cours",
            suggested_message="Collez d’abord la dernière réponse reçue afin de générer un message adapté sans inventer le contexte.",
        )

    persona = lead_persona(lead)
    follow_ups = {
        "apporteur": f"Bonjour {first_name}, je me permets une courte relance. Pour être concret, je cible des missions d’architecture, de gouvernance ou de delivery Salesforce sur des environnements complexes. Avez-vous un besoin à venir correspondant, ou un responsable de practice vers qui m’orienter ?",
        "decideur": f"Bonjour {first_name}, je me permets de revenir vers vous avec une question concrète : parmi l’adoption, la conversion, le renouvellement et la qualité de service, quel indicateur CRM cherchez-vous aujourd’hui à améliorer en priorité ? Je serais ravi de partager quelques retours d’expérience lors d’un échange court.",
        "partenaire": f"Bonjour {first_name}, je me permets une courte relance au sujet d’une possible complémentarité. Je serais intéressé par un échange de 15 minutes pour identifier les types de missions ou de compétences sur lesquels nous pourrions nous recommander mutuellement.",
        "contact": f"Bonjour {first_name}, je me permets une courte relance. Êtes-vous la bonne personne pour échanger sur des sujets d’architecture et de transformation Salesforce, ou pourriez-vous m’orienter vers l’interlocuteur concerné ?",
    }
    return LeadCoachResult(
        situation="Premier message envoyé, sans réponse pour le moment.", objective="Obtenir une réponse avec une question adaptée au rôle du contact.",
        next_action="Envoyer cette relance à J+3. À J+8, partager une preuve concrète ; à J+21, passer la piste en « À nourrir ».", suggested_stage="message_envoye",
        suggested_message=follow_ups[persona],
    )


def sales_agent_plan(lead: Lead) -> dict | None:
    """Return the next useful conversion action without sending anything externally."""
    now = datetime.utcnow()
    age = max(0, (now - (lead.updated_at or lead.created_at or now)).days)
    first_name = lead.name.split()[0] if lead.name else ""
    base_priority = min(95, max(20, lead.score) + {
        "qualifiee": 15,
        "echange_en_cours": 20,
        "rendez_vous_planifie": 25,
        "mission_detectee": 25,
        "mise_en_relation": 20,
    }.get(lead.stage, 0))

    if lead.stage in ("nouvelle", "a_contacter"):
        persona, objective, message = post_acceptance_message(lead, first_name)
        return {
            "action_type": "premier_message",
            "title": f"Contacter {lead.name}",
            "rationale": f"Connexion acceptée · profil {persona}. {objective}",
            "message": message,
            "target_stage": "message_envoye",
            "priority": base_priority,
        }
    if lead.stage == "message_envoye" and age >= 3:
        follow_ups = {
            "apporteur": f"Bonjour {first_name}, je me permets une courte relance. Pour être concret, je cible des missions d’architecture, de gouvernance ou de delivery Salesforce sur des environnements complexes. Avez-vous un besoin à venir correspondant, ou un responsable de practice vers qui m’orienter ?",
            "decideur": f"Bonjour {first_name}, je me permets de revenir vers vous avec une question concrète : parmi l’adoption, la conversion, le renouvellement et la qualité de service, quel indicateur CRM cherchez-vous aujourd’hui à améliorer en priorité ? Je serais ravi de partager quelques retours d’expérience lors d’un échange court.",
            "partenaire": f"Bonjour {first_name}, je me permets une courte relance au sujet d’une possible complémentarité. Je serais intéressé par un échange de 15 minutes pour identifier les types de missions ou de compétences sur lesquels nous pourrions nous recommander mutuellement.",
            "contact": f"Bonjour {first_name}, je me permets une courte relance. Êtes-vous la bonne personne pour échanger sur des sujets d’architecture et de transformation Salesforce, ou pourriez-vous m’orienter vers l’interlocuteur concerné ?",
        }
        return {
            "action_type": "relance",
            "title": f"Relancer {lead.name}",
            "rationale": f"Aucune progression enregistrée depuis {age} jours. La relance est adaptée au rôle du contact.",
            "message": follow_ups[lead_persona(lead)],
            "target_stage": "message_envoye",
            "priority": min(100, base_priority + min(age, 15)),
        }
    if lead.stage in ("qualifiee", "echange_en_cours") and age >= 1:
        return {
            "action_type": "qualification",
            "title": f"Qualifier le besoin avec {lead.name}",
            "rationale": "La conversation est engagée : il faut obtenir un besoin, un calendrier et le décideur concerné.",
            "message": (
                f"Bonjour {first_name}, pour avancer concrètement, pourriez-vous me préciser les priorités Salesforce "
                "concernées, le calendrier envisagé et les interlocuteurs impliqués dans la décision ? "
                "Je vous propose un échange de 15 minutes pour vérifier rapidement l’adéquation avec mon expérience."
            ),
            "target_stage": "echange_en_cours",
            "priority": base_priority,
        }
    if lead.stage == "rendez_vous_planifie":
        return {
            "action_type": "preparation_rendez_vous",
            "title": f"Préparer le rendez-vous avec {lead.name}",
            "rationale": "Préparer trois questions : besoin prioritaire, calendrier de décision et critères de réussite.",
            "message": "",
            "target_stage": "rendez_vous_planifie",
            "priority": 100,
        }
    if lead.stage == "mission_detectee" and age >= 1:
        return {
            "action_type": "qualification_mission",
            "title": f"Qualifier la mission détectée avec {lead.name}",
            "rationale": "Une mission existe : sécuriser le périmètre, le TJM, le rythme hybride et le processus de décision.",
            "message": (
                f"Bonjour {first_name}, merci pour cette piste. Pour confirmer rapidement mon positionnement, "
                "pourriez-vous me partager le périmètre détaillé, la date de démarrage, la durée, le rythme sur site, "
                "le budget/TJM et les prochaines étapes du processus ?"
            ),
            "target_stage": "mission_detectee",
            "priority": 100,
        }
    if lead.stage in ("mise_en_relation", "partenaire_apporteur") and age >= 7:
        return {
            "action_type": "activation_reseau",
            "title": f"Réactiver la relation avec {lead.name}",
            "rationale": "Le contact peut devenir apporteur d’affaires : entretenir la relation avec une demande précise et réciproque.",
            "message": (
                f"Bonjour {first_name}, je me permets de reprendre contact. Je reste disponible pour des missions "
                "d’architecture, de gouvernance ou de delivery Salesforce. De votre côté, y a-t-il un besoin sur lequel "
                "je pourrais vous aider ou une compétence de mon réseau que vous recherchez actuellement ?"
            ),
            "target_stage": "partenaire_apporteur",
            "priority": base_priority,
        }
    if lead.stage in ("a_nourrir", "a_reactiver") and age >= 14:
        return {
            "action_type": "reactivation",
            "title": f"Revenir dans le radar de {lead.name}",
            "rationale": f"Relation inactive depuis {age} jours : apporter une information utile plutôt qu’une simple relance.",
            "message": (
                f"Bonjour {first_name}, je me permets de reprendre contact. Je travaille actuellement sur les leviers "
                "d’harmonisation et d’agentification du CRM pour améliorer adoption, conversion et qualité de service. "
                "Est-ce un sujet présent dans vos priorités ou celles de vos clients cette année ?"
            ),
            "target_stage": "a_nourrir",
            "priority": base_priority,
        }
    return None


def sales_agent_briefing(db: Session) -> SalesAgentBriefing:
    now = datetime.utcnow()
    actions = db.scalars(
        select(LeadAction)
        .where(LeadAction.status.in_(("pending", "snoozed")))
        .order_by(LeadAction.due_at.asc(), LeadAction.priority.desc())
    ).all()
    items = [SalesAgentActionOut(
        id=action.id,
        lead_id=action.lead_id,
        lead_name=action.lead.name,
        lead_company=action.lead.company,
        linkedin_url=action.lead.linkedin_url,
        action_type=action.action_type,
        title=action.title,
        rationale=action.rationale,
        message=action.message,
        target_stage=action.target_stage,
        priority=action.priority,
        status=action.status,
        due_at=action.due_at,
        created_at=action.created_at,
    ) for action in actions]
    active_leads = db.scalar(select(func.count()).select_from(Lead).where(Lead.stage != "hors_cible")) or 0
    due = [action for action in actions if action.due_at <= now]
    return SalesAgentBriefing(
        generated_at=now,
        active_leads=active_leads,
        due_actions=len(due),
        overdue_actions=sum(action.due_at.date() < now.date() for action in due),
        high_priority_actions=sum(action.priority >= 75 for action in due),
        actions=items,
    )


@app.post("/api/sales-agent/run", response_model=SalesAgentBriefing)
def run_sales_agent(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    active_lead_ids = set(db.scalars(
        select(LeadAction.lead_id).where(LeadAction.status.in_(("pending", "snoozed")))
    ).all())
    leads = db.scalars(select(Lead).where(Lead.stage != "hors_cible")).all()
    for lead in leads:
        if lead.id in active_lead_ids:
            continue
        plan = sales_agent_plan(lead)
        if not plan:
            continue
        db.add(LeadAction(lead_id=lead.id, due_at=now, status="pending", **plan))
    for action in db.scalars(
        select(LeadAction).where(LeadAction.status == "snoozed", LeadAction.due_at <= now)
    ).all():
        action.status = "pending"
    db.commit()
    return sales_agent_briefing(db)


@app.get("/api/sales-agent/briefing", response_model=SalesAgentBriefing)
def get_sales_agent_briefing(db: Session = Depends(get_db)):
    return sales_agent_briefing(db)


@app.post("/api/sales-agent/actions/{action_id}/confirm", response_model=SalesAgentBriefing)
def confirm_sales_agent_action(action_id: int, payload: SalesAgentConfirm, db: Session = Depends(get_db)):
    action = db.get(LeadAction, action_id)
    if not action or action.status not in ("pending", "snoozed"):
        raise HTTPException(404, "Action commerciale active introuvable")
    if action.action_type != "preparation_rendez_vous" and not payload.message.strip():
        raise HTTPException(422, "Le message validé ne peut pas être vide")
    if payload.message.strip():
        action.message = payload.message.strip()
    action.status = "completed"
    action.completed_at = datetime.utcnow()
    action.lead.stage = action.target_stage
    action.lead.score, action.lead.score_details = score_lead(action.lead)
    db.commit()
    return sales_agent_briefing(db)


@app.post("/api/sales-agent/actions/{action_id}/snooze", response_model=SalesAgentBriefing)
def snooze_sales_agent_action(action_id: int, payload: SalesAgentSnooze, db: Session = Depends(get_db)):
    action = db.get(LeadAction, action_id)
    if not action or action.status not in ("pending", "snoozed"):
        raise HTTPException(404, "Action commerciale active introuvable")
    action.status = "snoozed"
    action.due_at = datetime.utcnow() + timedelta(days=payload.days)
    db.commit()
    return sales_agent_briefing(db)


@app.get("/api/automation/outreach/candidates", response_model=list[AutomatedOutreachCandidate])
def automated_outreach_candidates(limit: int = 10, db: Session = Depends(get_db)):
    """Return high-confidence first contacts for the authorized outreach runner."""
    limit = max(1, min(limit, 10))
    start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = db.scalar(
        select(func.count()).select_from(LeadAction).where(
            LeadAction.action_type == "auto_first_contact",
            LeadAction.completed_at >= start_of_day,
        )
    ) or 0
    remaining = max(0, 10 - sent_today)
    if not remaining:
        return []

    leads = db.scalars(
        select(Lead)
        .where(Lead.stage.in_(("nouvelle", "a_contacter")))
        .order_by(Lead.score.desc(), Lead.connected_on.desc(), Lead.created_at.desc())
    ).all()
    result = []
    for lead in leads:
        lead.score, lead.score_details = score_lead(lead)
        name_parts = [part.strip(".,- ") for part in lead.name.split() if part.strip(".,- ")]
        identity_is_clear = len(name_parts) >= 2 and all(len(part) > 1 for part in name_parts)
        if lead.score <= 80 or not identity_is_clear:
            continue
        _, _, message = post_acceptance_message(lead, lead.name.split()[0])
        identity = lead.score_details.get("identite_prospect", {})
        result.append(AutomatedOutreachCandidate(
            lead_id=lead.id,
            name=lead.name,
            company=lead.company,
            headline=lead.headline,
            linkedin_url=lead.linkedin_url,
            score=lead.score,
            prospect_type=identity.get("type", "contact à qualifier"),
            competencies=lead.score_details.get("competences", ["À qualifier"]),
            message=message,
        ))
        if len(result) >= min(limit, remaining):
            break
    # This is a read endpoint. Roll back the transient score refresh so a
    # simple candidate lookup cannot modify ``updated_at`` and postpone work.
    db.rollback()
    return result


@app.post("/api/automation/outreach/{lead_id}/sent", response_model=LeadOut)
def confirm_automated_outreach(lead_id: int, payload: AutomatedOutreachSent, db: Session = Depends(get_db)):
    """Record a message only after the external runner confirms the actual send."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Piste introuvable")
    lead.score, lead.score_details = score_lead(lead)
    if lead.score <= 80 or lead.stage not in ("nouvelle", "a_contacter"):
        raise HTTPException(409, "Cette piste n’est plus éligible au premier contact automatique")
    now = datetime.utcnow()
    action_type = "existing_first_contact" if payload.already_existed else "auto_first_contact"
    db.add(LeadAction(
        lead_id=lead.id,
        action_type=action_type,
        title=(f"Premier contact existant réconcilié pour {lead.name}" if payload.already_existed else f"Premier contact automatique envoyé à {lead.name}"),
        rationale=(
            "Conversation LinkedIn antérieure confirmée visuellement ; statut CRM rapproché sans nouvel envoi."
            if payload.already_existed else
            "Score supérieur à 80 et profil classé dans une cible CRM/Salesforce pertinente."
        ),
        message=payload.message.strip(),
        target_stage="message_envoye",
        priority=lead.score,
        status="completed",
        due_at=now,
        completed_at=now,
    ))
    lead.stage = "message_envoye"
    lead.score, lead.score_details = score_lead(lead)
    db.commit()
    db.refresh(lead)
    return lead


@app.get("/api/automation/follow-ups/candidates", response_model=list[AutomatedFollowupCandidate])
def automated_followup_candidates(limit: int = 5, db: Session = Depends(get_db)):
    """Return due post-Waalaxy messages, capped to five actual sends per day."""
    limit = max(1, min(limit, 5))
    now = datetime.utcnow()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = db.scalar(
        select(func.count()).select_from(LeadAction).where(
            LeadAction.action_type.in_(tuple(FOLLOWUP_ACTION_TYPES.values())),
            LeadAction.completed_at >= start_of_day,
        )
    ) or 0
    remaining = max(0, 5 - sent_today)
    if not remaining:
        return []

    leads = db.scalars(
        select(Lead)
        .where(Lead.stage == "message_envoye")
        .order_by(Lead.score.desc(), Lead.updated_at.asc())
    ).all()
    candidates = []
    for lead in leads:
        lead.score, lead.score_details = score_lead(lead)
        name_parts = [part.strip(".,- ") for part in lead.name.split() if part.strip(".,- ")]
        if lead.score <= 80 or len(name_parts) < 2 or not all(len(part) > 1 for part in name_parts):
            continue
        plan = automated_followup_plan(lead, db)
        if not plan or plan["due_at"] > now:
            continue
        identity = lead.score_details.get("identite_prospect", {})
        candidates.append(AutomatedFollowupCandidate(
            lead_id=lead.id,
            name=lead.name,
            company=lead.company,
            headline=lead.headline,
            linkedin_url=lead.linkedin_url,
            score=lead.score,
            prospect_type=identity.get("type", "contact à qualifier"),
            competencies=lead.score_details.get("competences", ["À qualifier"]),
            **plan,
        ))
        if len(candidates) >= min(limit, remaining):
            break
    # Keep candidate discovery read-only; message confirmation endpoints own
    # all persistent changes.
    db.rollback()
    return candidates


@app.post("/api/automation/follow-ups/{lead_id}/sent", response_model=LeadOut)
def confirm_automated_followup(lead_id: int, payload: AutomatedFollowupSent, db: Session = Depends(get_db)):
    """Record a follow-up only after the runner confirms the message was sent."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Piste introuvable")
    lead.score, lead.score_details = score_lead(lead)
    plan = automated_followup_plan(lead, db)
    if not plan or plan["due_at"] > datetime.utcnow() or plan["sequence_step"] != payload.sequence_step:
        raise HTTPException(409, "Cette relance n’est plus éligible ou ne correspond pas à la prochaine étape")
    now = datetime.utcnow()
    target_stage = "a_nourrir" if payload.sequence_step == "reactivation_j30" else "message_envoye"
    db.add(LeadAction(
        lead_id=lead.id,
        action_type=FOLLOWUP_ACTION_TYPES[payload.sequence_step],
        title=f"Suivi automatique {payload.sequence_step} envoyé à {lead.name}",
        rationale=plan["objective"],
        message=payload.message.strip(),
        target_stage=target_stage,
        priority=lead.score,
        status="completed",
        due_at=plan["due_at"],
        completed_at=now,
    ))
    lead.stage = target_stage
    lead.score, lead.score_details = score_lead(lead)
    db.commit()
    db.refresh(lead)
    return lead


@app.post("/api/automation/follow-ups/{lead_id}/response", response_model=LeadOut)
def record_automated_lead_response(lead_id: int, payload: AutomatedLeadResponse, db: Session = Depends(get_db)):
    """Stop the no-response sequence as soon as an inbound LinkedIn reply is detected."""
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Piste introuvable")
    now = datetime.utcnow()
    db.add(LeadAction(
        lead_id=lead.id,
        action_type="inbound_response",
        title=f"Réponse LinkedIn reçue de {lead.name}",
        rationale="Le prospect a répondu : la séquence automatique sans réponse est arrêtée et la qualification devient prioritaire.",
        message=payload.message.strip(),
        target_stage="echange_en_cours",
        priority=100,
        status="completed",
        due_at=now,
        completed_at=now,
    ))
    lead.stage = "echange_en_cours"
    lead.score, lead.score_details = score_lead(lead)
    db.commit()
    db.refresh(lead)
    return lead


@app.get("/api/opportunities", response_model=list[OpportunityOut])
def list_opportunities(db: Session = Depends(get_db)):
    return db.scalars(select(Opportunity).order_by(Opportunity.score.desc(), Opportunity.updated_at.desc())).all()


@app.post("/api/opportunities", response_model=OpportunityOut)
def create_opportunity(payload: OpportunityCreate, db: Session = Depends(get_db)):
    opportunity = Opportunity(**payload.model_dump())
    profile = db.scalar(select(CandidateProfile).limit(1))
    opportunity.score, opportunity.score_details = score_opportunity(opportunity, profile)
    db.add(opportunity)
    db.commit()
    db.refresh(opportunity)
    return opportunity


@app.patch("/api/opportunities/{opportunity_id}/stage", response_model=OpportunityOut)
def update_stage(opportunity_id: int, payload: OpportunityStageUpdate, db: Session = Depends(get_db)):
    opportunity = db.get(Opportunity, opportunity_id)
    if not opportunity:
        raise HTTPException(404, "Mission introuvable")
    opportunity.stage = payload.stage
    db.commit()
    db.refresh(opportunity)
    return opportunity


@app.post("/api/opportunities/{opportunity_id}/coach", response_model=OpportunityCoachResult)
def coach_opportunity(opportunity_id: int, db: Session = Depends(get_db)):
    opportunity = db.get(Opportunity, opportunity_id)
    if not opportunity:
        raise HTTPException(404, "Mission introuvable")

    company = opportunity.company.strip() or "votre structure"
    title = opportunity.title.strip()
    if opportunity.stage in {"nouvelle", "qualifiee"}:
        return OpportunityCoachResult(
            situation=f"La mission {title} mérite une candidature directe qui démontre immédiatement la valeur du profil.",
            objective="Donner au recruteur des raisons concrètes de retenir le profil et obtenir un entretien de qualification.",
            next_action="Vérifier les éléments propres à l'offre, joindre le CV ciblé, puis envoyer cette candidature sans attendre une demande d'informations complémentaire.",
            suggested_stage="contact",
            suggested_message=f"Bonjour,\n\nJe vous contacte pour vous proposer ma candidature à la mission « {title} » publiée par {company}. Le besoin correspond directement à mon positionnement d’Architecte CRM/Salesforce senior, avec plus de 20 ans d’expérience SI, dont plus de 10 ans sur Salesforce.\n\nJ’interviens de façon opérationnelle sur le cadrage, l’architecture de solution, l’alignement métier–SI et la sécurisation du delivery. Chez TotalEnergies, j’ai notamment repris un programme Salesforce complexe et contribué à augmenter d’environ 40 % le taux de résolution des incidents. J’ai également réduit de 65 % les délais d’activation grâce à la rationalisation des processus Salesforce.\n\nJe peux ainsi être rapidement autonome sur cette mission, aussi bien dans les arbitrages d’architecture que dans la coordination des équipes et des parties prenantes. Je suis disponible immédiatement en freelance, en Île-de-France ou en mode hybride.\n\nJe vous joins mon CV ciblé. Seriez-vous disponible pour un échange de 15 minutes afin de valider mon adéquation avec le besoin ?\n\nBien cordialement,\nBoubacar DIABY",
        )

    return OpportunityCoachResult(
        situation=f"Une prise de contact est déjà enregistrée pour la mission {title}.",
        objective="Obtenir un retour concret sans répéter la candidature initiale.",
        next_action="Relancer brièvement avec une question simple sur l'avancement du besoin.",
        suggested_stage=opportunity.stage,
        suggested_message=f"Bonjour, je reviens vers vous au sujet de la mission {title}. Mon expérience en architecture et delivery CRM/Salesforce reste très alignée avec le besoin présenté. Le processus de sélection est-il toujours en cours ? Je reste disponible pour un échange rapide et peux vous renvoyer mon CV ciblé si nécessaire.",
    )


@app.post("/api/ats/analyze", response_model=ATSResult)
def analyze_ats(payload: ATSRequest, db: Session = Depends(get_db)):
    opportunity = db.get(Opportunity, payload.opportunity_id)
    profile = db.scalar(select(CandidateProfile).limit(1))
    if not opportunity:
        raise HTTPException(404, "Mission introuvable")
    if not profile.cv_text and not profile.skills:
        raise HTTPException(400, "Complète ton profil et colle le contenu du CV avant l'analyse")
    return build_ats_result(opportunity, profile)
