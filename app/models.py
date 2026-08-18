from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="Mon profil")
    title: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    skills: Mapped[list] = mapped_column(JSON, default=list)
    preferred_roles: Mapped[list] = mapped_column(JSON, default=list)
    preferred_locations: Mapped[list] = mapped_column(JSON, default=list)
    minimum_daily_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    availability: Mapped[str] = mapped_column(String(120), default="")
    cv_text: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    company: Mapped[str] = mapped_column(String(160), default="")
    role: Mapped[str] = mapped_column(String(160), default="")
    linkedin_url: Mapped[str] = mapped_column(String(500), default="")
    email: Mapped[str] = mapped_column(String(240), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    company: Mapped[str] = mapped_column(String(180), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(160), default="")
    work_mode: Mapped[str] = mapped_column(String(80), default="")
    daily_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_url: Mapped[str] = mapped_column(String(700), default="")
    stage: Mapped[str] = mapped_column(String(60), default="nouvelle")
    score: Mapped[int] = mapped_column(Integer, default=0)
    score_details: Mapped[dict] = mapped_column(JSON, default=dict)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    contact: Mapped[Contact | None] = relationship()
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Interaction(Base):
    __tablename__ = "interactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    kind: Mapped[str] = mapped_column(String(80), default="note")
    content: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(240))
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

