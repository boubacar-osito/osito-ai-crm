from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ProfilePayload(BaseModel):
    name: str = "Mon profil"
    title: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    preferred_roles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    minimum_daily_rate: int | None = None
    availability: str = ""
    cv_text: str = ""
    soft_skill_profile: str = ""
    work_preferences: str = ""
    development_points: str = ""


class ProfileOut(ProfilePayload):
    model_config = ConfigDict(from_attributes=True)
    id: int
    updated_at: datetime


class ContactCreate(BaseModel):
    name: str
    company: str = ""
    role: str = ""
    linkedin_url: str = ""
    email: str = ""
    notes: str = ""


class OpportunityCreate(BaseModel):
    title: str
    company: str = ""
    description: str = ""
    location: str = ""
    work_mode: str = ""
    daily_rate: int | None = None
    source_url: str = ""
    published_on: date | None = None
    published_on_is_estimated: bool = False
    stage: str = "nouvelle"
    contact_id: int | None = None


class OpportunityOut(OpportunityCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    score: int
    score_details: dict
    created_at: datetime
    updated_at: datetime


class OpportunityStageUpdate(BaseModel):
    stage: Literal["nouvelle", "qualifiee", "contact", "entretien", "proposition", "gagnee", "perdue"]


class ATSRequest(BaseModel):
    opportunity_id: int


class ATSResult(BaseModel):
    match_score: int
    matched_keywords: list[str]
    missing_keywords: list[str]
    suggested_title: str
    tailored_summary: str
    reordered_skills: list[str]
    warnings: list[str]


class OpportunityCoachResult(BaseModel):
    situation: str
    objective: str
    next_action: str
    suggested_stage: Literal["nouvelle", "qualifiee", "contact", "entretien", "proposition"]
    suggested_message: str


class LeadCreate(BaseModel):
    name: str
    headline: str = ""
    company: str = ""
    linkedin_url: str
    connected_on: date | None = None
    source: str = "LinkedIn / Waalaxy"
    stage: str = "nouvelle"
    notes: str = ""


class LeadImportBatch(BaseModel):
    leads: list[LeadCreate] = Field(min_length=1, max_length=200)


class LeadImportResult(BaseModel):
    examined: int
    created: int
    duplicates: int
    lead_ids: list[int]
    total_leads: int
    imported_at: datetime


class LeadImportStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source: str
    examined: int
    created: int
    duplicates: int
    total_leads: int
    imported_at: datetime


class LeadOut(LeadCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    score: int
    score_details: dict
    created_at: datetime
    updated_at: datetime


class LeadStageUpdate(BaseModel):
    stage: Literal[
        "nouvelle", "a_contacter", "qualifiee", "message_envoye", "echange_en_cours",
        "rendez_vous_planifie", "mission_detectee", "mise_en_relation",
        "partenaire_apporteur", "a_nourrir", "a_reactiver", "hors_cible",
    ]


class LeadCoachRequest(BaseModel):
    latest_message: str = ""


class LeadCoachResult(BaseModel):
    situation: str
    objective: str
    next_action: str
    suggested_stage: Literal[
        "nouvelle", "a_contacter", "qualifiee", "message_envoye", "echange_en_cours",
        "rendez_vous_planifie", "mission_detectee", "mise_en_relation",
        "partenaire_apporteur", "a_nourrir", "a_reactiver", "hors_cible",
    ]
    suggested_message: str


class SalesAgentActionOut(BaseModel):
    id: int
    lead_id: int
    lead_name: str
    lead_company: str
    linkedin_url: str
    action_type: str
    title: str
    rationale: str
    message: str
    target_stage: str
    priority: int
    status: str
    due_at: datetime
    created_at: datetime


class SalesAgentBriefing(BaseModel):
    generated_at: datetime
    active_leads: int
    due_actions: int
    overdue_actions: int
    high_priority_actions: int
    actions: list[SalesAgentActionOut]


class SalesAgentConfirm(BaseModel):
    message: str = ""


class SalesAgentSnooze(BaseModel):
    days: int = Field(default=3, ge=1, le=30)


class AutomatedOutreachCandidate(BaseModel):
    lead_id: int
    name: str
    company: str
    headline: str
    linkedin_url: str
    score: int
    prospect_type: str
    competencies: list[str]
    message: str


class AutomatedOutreachSent(BaseModel):
    message: str = Field(min_length=1)
    already_existed: bool = False


class AutomatedFollowupCandidate(BaseModel):
    lead_id: int
    name: str
    company: str
    headline: str
    linkedin_url: str
    score: int
    prospect_type: str
    competencies: list[str]
    sequence_step: Literal["qualification_j3", "valeur_j10", "reactivation_j30"]
    objective: str
    message: str
    due_at: datetime


class AutomatedFollowupSent(BaseModel):
    sequence_step: Literal["qualification_j3", "valeur_j10", "reactivation_j30"]
    message: str = Field(min_length=1)


class AutomatedLeadResponse(BaseModel):
    message: str = Field(min_length=1)
