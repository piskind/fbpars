from datetime import datetime
from enum import Enum
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum as SAEnum, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint, JSON, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import ARRAY, JSONB


class Base(DeclarativeBase):
    pass


class TaskType(str, Enum):
    DISCOVERY = "discovery"
    REFRESH = "refresh"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class ModerationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AdMediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    CAROUSEL = "carousel"
    UNKNOWN = "unknown"


class ParsingConfig(Base):
    __tablename__ = "parsing_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    keyword: Mapped[str] = mapped_column(String(255))
    country: Mapped[str] = mapped_column(String(8))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    vertical: Mapped[str] = mapped_column(String(32), default="nutra", index=True)
    partner: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    languages: Mapped[list | None] = mapped_column(ARRAY(String), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("keyword", "country", name="uq_keyword_country"),
    )


class ParsingTask(Base):
    __tablename__ = "parsing_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_type: Mapped[TaskType] = mapped_column(SAEnum(TaskType, name="task_type"))
    config_id: Mapped[int | None] = mapped_column(ForeignKey("parsing_configs.id", ondelete="SET NULL"), nullable=True)
    ad_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    status: Mapped[TaskStatus] = mapped_column(SAEnum(TaskStatus, name="task_status"), default=TaskStatus.PENDING)

    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ads_found: Mapped[int] = mapped_column(Integer, default=0)
    ads_new: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_tasks_status_priority_scheduled", "status", "priority", "scheduled_at"),
    )


class Ad(Base):
    __tablename__ = "ads"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    country: Mapped[str] = mapped_column(String(8), index=True)
    keyword: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    page_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    page_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    page_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    link_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_url: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)

    media_type: Mapped[AdMediaType] = mapped_column(SAEnum(AdMediaType, name="ad_media_type"), default=AdMediaType.UNKNOWN)
    platforms: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)

    lead_form: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    app_store: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ecom_platform: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    days_active: Mapped[int] = mapped_column(Integer, default=0)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    vertical: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    reach: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    reach_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    eu_countries: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    used_in_ads_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spend_estimate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    creatives: Mapped[list["Creative"]] = relationship(back_populates="ad", cascade="all, delete-orphan")
    moderation: Mapped["ModerationEntry"] = relationship(back_populates="ad", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_ads_eu_countries_gin", "eu_countries", postgresql_using="gin"),
    )


class Creative(Base):
    __tablename__ = "creatives"

    id: Mapped[int] = mapped_column(primary_key=True)
    ad_id: Mapped[int] = mapped_column(ForeignKey("ads.id", ondelete="CASCADE"), index=True)

    media_type: Mapped[AdMediaType] = mapped_column(SAEnum(AdMediaType, name="ad_media_type"))
    original_url: Mapped[str] = mapped_column(Text)
    s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    s3_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    phash: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    md5: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    downloaded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ad: Mapped["Ad"] = relationship(back_populates="creatives")


class ModerationEntry(Base):
    __tablename__ = "moderation_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    ad_id: Mapped[int] = mapped_column(ForeignKey("ads.id", ondelete="CASCADE"), unique=True)
    status: Mapped[ModerationStatus] = mapped_column(SAEnum(ModerationStatus, name="moderation_status"), default=ModerationStatus.PENDING, index=True)

    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ad: Mapped["Ad"] = relationship(back_populates="moderation")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClientUser(Base):
    __tablename__ = "client_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    referral_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ParserRun(Base):
    __tablename__ = "parser_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="triggered")
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    log_tail: Mapped[str | None] = mapped_column(Text, nullable=True)


class Proxy(Base):
    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(128), unique=True)
    proxy_type: Mapped[str] = mapped_column(String(16), default="socks5")
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rotate_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())