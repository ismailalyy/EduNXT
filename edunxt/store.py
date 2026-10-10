"""Persistence: contacts, cases, a message log and a small job queue (SQLite by default)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class CaseStatus:
    AWAITING_PROFILE = "AWAITING_PROFILE"
    DRAFTING = "DRAFTING"
    IN_REVIEW = "IN_REVIEW"
    REVISING = "REVISING"
    APPROVED = "APPROVED"
    FAILED = "FAILED"

    OPEN = (AWAITING_PROFILE, DRAFTING, IN_REVIEW, REVISING, FAILED)


class Contact(Base):
    __tablename__ = "contacts"
    phone: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16), default="client")  # client | founder
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_inbound: Mapped[datetime | None] = mapped_column(DateTime)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_phone: Mapped[str | None] = mapped_column(String(32), index=True)
    founder_phone: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(24), default=CaseStatus.AWAITING_PROFILE)
    language: Mapped[str | None] = mapped_column(String(16))
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    draft: Mapped[dict | None] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def ref(self) -> str:
        return f"EN-{self.id:04d}"


class MessageLog(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wa_id: Mapped[str | None] = mapped_column(String(128), unique=True)  # provider message id
    direction: Mapped[str] = mapped_column(String(8))  # in | out
    phone: Mapped[str] = mapped_column(String(32), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"))
    kind: Mapped[str] = mapped_column(String(16), default="text")  # text | document | template
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="sent")  # sent | pending | failed | received
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # inbound | draft | revise
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Store:
    def __init__(self, database_url: str):
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, connect_args=connect_args)
        Base.metadata.create_all(self.engine)
        self.session: sessionmaker[Session] = sessionmaker(self.engine, expire_on_commit=False)
        self._claim_lock = threading.Lock()

    def enqueue(self, kind: str, payload: dict) -> int:
        with self.session.begin() as s:
            job = Job(kind=kind, payload=payload)
            s.add(job)
            s.flush()
            return job.id

    def claim_next_job(self, kinds: tuple[str, ...]) -> Job | None:
        with self._claim_lock, self.session.begin() as s:
            query = select(Job).where(Job.status == "queued", Job.kind.in_(kinds)).order_by(Job.id).limit(1)
            job = s.scalars(query).first()
            if job:
                job.status = "running"
                job.attempts += 1
            return job

    def has_queued(self, kinds: tuple[str, ...]) -> bool:
        with self.session() as s:
            return s.scalar(select(Job.id).where(Job.status == "queued", Job.kind.in_(kinds)).limit(1)) is not None

    def finish_job(self, job_id: int, error: str | None = None) -> None:
        with self.session.begin() as s:
            job = s.get(Job, job_id)
            job.status = "failed" if error else "done"
            job.error = error

    def requeue_running(self) -> None:
        """Jobs left 'running' by a crash or restart are picked up again."""
        with self.session.begin() as s:
            for job in s.scalars(select(Job).where(Job.status == "running")):
                job.status = "queued"
