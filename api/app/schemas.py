from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AdminLoginIn(BaseModel):
    login: str
    password: str


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    login: str
    is_active: bool


class ParsingConfigIn(BaseModel):
    keyword: str
    country: str
    is_active: bool = True
    notes: str | None = None


ParsingConfigCreate = ParsingConfigIn


class ParsingConfigUpdate(BaseModel):
    keyword: str | None = None
    country: str | None = None
    is_active: bool | None = None
    notes: str | None = None


class ParsingConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    keyword: str
    country: str
    is_active: bool
    notes: str | None
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
    page_id: str | None
    page_name: str | None
    page_url: str | None
    body: str | None
    cta_text: str | None
    link_url: str | None
    display_url: str | None
    media_type: str
    started_at: datetime | None
    is_active: bool
    days_active: int
    first_seen_at: datetime
    last_seen_at: datetime
    creatives: list[CreativeOut] = []


class ModerationItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    created_at: datetime
    reviewed_at: datetime | None
    reject_reason: str | None
    ad: AdOut


class ModerationActionIn(BaseModel):
    status: str
    reject_reason: str | None = None


class ClientUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    email_verified: bool
    referral_source: str | None
    created_at: datetime
    last_login_at: datetime | None
