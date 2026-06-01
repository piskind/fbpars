from datetime import datetime
from pydantic import BaseModel, ConfigDict


class AdminLoginIn(BaseModel):
    login: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    login: str
    is_active: bool
    created_at: datetime


class ParsingConfigIn(BaseModel):
    keyword: str
    country: str
    vertical: str = "nutra"
    is_active: bool = True
    notes: str | None = None
    partner: str | None = None
    category: str | None = None


ParsingConfigCreate = ParsingConfigIn


class ParsingConfigUpdate(BaseModel):
    keyword: str | None = None
    country: str | None = None
    vertical: str | None = None
    is_active: bool | None = None
    notes: str | None = None
    partner: str | None = None
    category: str | None = None


class ParsingConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    keyword: str
    country: str
    vertical: str
    is_active: bool
    notes: str | None
    partner: str | None = None
    category: str | None = None
    created_at: datetime
    updated_at: datetime
    ads_count: int = 0
    last_parsed_at: datetime | None = None


class CreativeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    media_type: str
    s3_url: str | None
    original_url: str
    width: int | None
    height: int | None


class AdOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    library_id: str
    country: str
    keyword: str | None
    vertical: str | None = None
    page_id: str | None
    page_name: str | None
    page_url: str | None
    body: str | None
    cta_text: str | None
    link_url: str | None
    display_url: str | None
    media_type: str
    platforms: list[str] | None = None
    lead_form: bool = False
    language: str | None = None
    app_store: str | None = None
    ecom_platform: str | None = None
    ip: str | None = None
    started_at: datetime | None
    is_active: bool
    days_active: int
    first_seen_at: datetime
    last_seen_at: datetime
    duplicates_count: int = 0
    creatives: list[CreativeOut] = []


class ModerationItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    created_at: datetime
    reviewed_at: datetime | None
    reject_reason: str | None
    ad: AdOut


class ClientUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    email_verified: bool
    referral_source: str | None = None
    created_at: datetime
    last_login_at: datetime | None = None

class ModerationActionIn(BaseModel):
    status: str
    reject_reason: str | None = None

class ClientSignupIn(BaseModel):
    email: str
    password: str
    referral_source: str | None = None


class ClientLoginIn(BaseModel):
    email: str
    password: str


class ClientSignupOut(BaseModel):
    id: int
    email: str
    message: str = "check your email"
    verification_url: str | None = None


class ClientUserMe(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    email_verified: bool
    created_at: datetime
    last_login_at: datetime | None


class ModerationListOut(BaseModel):
    items: list[ModerationItemOut]
    total: int


class ParserRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    triggered_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    status: str
    stats: dict | None
    log_tail: str | None = None


class ParserStatusOut(BaseModel):
    running: bool
    last_run: ParserRunOut | None = None
    recent_runs: list[ParserRunOut] = []