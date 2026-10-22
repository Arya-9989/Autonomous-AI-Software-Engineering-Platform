"""Persistent state: jobs, their stage runs, generated files and the event log."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetimes on every backend (SQLite drops tzinfo otherwise)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("naive datetimes are not allowed")
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


def _enum[E: StrEnum](enum_cls: type[E]) -> Enum:
    return Enum(
        enum_cls,
        native_enum=False,
        length=16,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_created_at", "status", "created_at"),
        Index("ix_jobs_owner_created_at", "owner", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    owner: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(200))
    spec: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.QUEUED)
    current_stage: Mapped[str | None] = mapped_column(String(32))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    project_name: Mapped[str | None] = mapped_column(String(64))
    requirements: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    architecture: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    review: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    test_report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    stages: Mapped[list[StageRun]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="StageRun.position",
        lazy="selectin",
    )


class StageRun(Base):
    __tablename__ = "stage_runs"
    __table_args__ = (UniqueConstraint("job_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[StageStatus] = mapped_column(_enum(StageStatus), default=StageStatus.PENDING)
    summary: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    job: Mapped[Job] = relationship(back_populates="stages")


class JobFile(Base):
    __tablename__ = "job_files"
    __table_args__ = (UniqueConstraint("job_id", "path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text, default="")


class JobEvent(Base):
    __tablename__ = "job_events"

    # BIGINT on PostgreSQL; INTEGER on SQLite so it aliases ROWID and autoincrements.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
