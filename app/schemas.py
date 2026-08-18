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
    stage: str = "nouvelle"
    contact_id: int | None = None


class OpportunityOut(OpportunityCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    score: int
    score_details: dict
    created_at: datetime
    updated_at: datetime


class StageUpdate(BaseModel):
    stage: str


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


class LeadCreate(BaseModel):
    name: str
    headline: str = ""
    company: str = ""
    linkedin_url: str
    connected_on: date | None = None
    source: str = "LinkedIn / Waalaxy"
    stage: str = "nouvelle"
    notes: str = ""


class LeadOut(LeadCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    score: int
    score_details: dict
    created_at: datetime
    updated_at: datetime


class LeadStageUpdate(BaseModel):
    stage: Literal["nouvelle", "a_contacter", "message_envoye", "echange_en_cours", "mission_detectee", "a_reactiver", "hors_cible"]


class LeadCoachRequest(BaseModel):
    latest_message: str = ""


class LeadCoachResult(BaseModel):
    situation: str
    objective: str
    next_action: str
    suggested_stage: Literal["nouvelle", "a_contacter", "message_envoye", "echange_en_cours", "mission_detectee", "a_reactiver", "hors_cible"]
    suggested_message: str
