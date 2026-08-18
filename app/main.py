from contextlib import asynccontextmanager
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
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, engine, get_db
from .models import CandidateDocument, CandidateProfile, Contact, Lead, Opportunity
from .schemas import ATSRequest, ATSResult, ContactCreate, LeadCoachRequest, LeadCoachResult, LeadCreate, LeadOut, LeadStageUpdate, OpportunityCreate, OpportunityOut, ProfileOut, ProfilePayload, StageUpdate
from .scoring import build_ats_result, score_lead, score_opportunity


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
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


@app.patch("/api/leads/{lead_id}/stage", response_model=LeadOut)
def update_lead_stage(lead_id: int, payload: LeadStageUpdate, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Piste introuvable")
    lead.stage = payload.stage
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
            situation="Le contact a répondu : la conversation est engagée.", objective="Qualifier l'existence d'un besoin, son calendrier et le décideur.",
            next_action="Répondre en proposant un échange de 15 minutes et demander les besoins prioritaires.", suggested_stage="echange_en_cours",
            suggested_message=f"Bonjour {first_name}, merci pour votre retour. Pour voir rapidement si mon profil peut répondre à l’un de vos besoins, seriez-vous disponible pour un échange de 15 minutes cette semaine ? Je pourrai vous présenter mes expériences récentes en architecture CRM/Salesforce et comprendre vos priorités actuelles ou à venir.",
        )

    if lead.stage in ("nouvelle", "a_contacter"):
        company_context = f" chez {lead.company}" if lead.company else ""
        return LeadCoachResult(
            situation="Cette piste est qualifiée, mais aucun premier message n'a encore été envoyé.",
            objective="Démarrer une conversation et vérifier si le contact traite des besoins correspondant au profil.",
            next_action="Envoyer ce premier message maintenant, puis classer la piste en « Message envoyé ».",
            suggested_stage="message_envoye",
            suggested_message=f"Bonjour {first_name}, je suis Architecte CRM/Solution senior, spécialisé Salesforce et transformation SI, et actuellement disponible pour une mission freelance. Votre activité{company_context} m’amène à vous contacter : accompagnez-vous actuellement des clients ayant des besoins en architecture Salesforce, cadrage CRM ou transformation SI ? Je peux vous transmettre mon CV et mes disponibilités si mon profil peut correspondre à l’un de vos besoins actuels ou à venir.",
        )

    if lead.stage == "a_reactiver":
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

    return LeadCoachResult(
        situation="Premier message envoyé, sans réponse pour le moment.", objective="Obtenir une réponse en apportant un élément concret et facile à qualifier.",
        next_action="Relancer 3 jours ouvrés après le premier message. Ne pas renvoyer une présentation générale.", suggested_stage="message_envoye",
        suggested_message=f"Bonjour {first_name}, je me permets une courte relance. J’interviens sur des missions d’architecture CRM/Salesforce, notamment sur le cadrage, la conception de solutions et l’alignement métier–SI. Avez-vous actuellement, ou prochainement, un besoin sur lequel ce positionnement pourrait être pertinent ? Je peux vous transmettre mon CV ciblé et mes disponibilités si utile.",
    )


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
def update_stage(opportunity_id: int, payload: StageUpdate, db: Session = Depends(get_db)):
    opportunity = db.get(Opportunity, opportunity_id)
    if not opportunity:
        raise HTTPException(404, "Mission introuvable")
    opportunity.stage = payload.stage
    db.commit()
    db.refresh(opportunity)
    return opportunity


@app.post("/api/ats/analyze", response_model=ATSResult)
def analyze_ats(payload: ATSRequest, db: Session = Depends(get_db)):
    opportunity = db.get(Opportunity, payload.opportunity_id)
    profile = db.scalar(select(CandidateProfile).limit(1))
    if not opportunity:
        raise HTTPException(404, "Mission introuvable")
    if not profile.cv_text and not profile.skills:
        raise HTTPException(400, "Complète ton profil et colle le contenu du CV avant l'analyse")
    return build_ats_result(opportunity, profile)
