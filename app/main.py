from contextlib import asynccontextmanager
import hmac
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth, OAuthError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, engine, get_db
from .models import CandidateProfile, Contact, Opportunity
from .schemas import ATSRequest, ATSResult, ContactCreate, OpportunityCreate, OpportunityOut, ProfileOut, ProfilePayload, StageUpdate
from .scoring import build_ats_result, score_opportunity


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


@app.post("/api/contacts")
def create_contact(payload: ContactCreate, db: Session = Depends(get_db)):
    contact = Contact(**payload.model_dump())
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


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
