from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, field_validator


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


_VALID_CONFIG_TYPES = {"keyword", "filters", "fanpage"}
_VALID_ACTIVE_STATUS = {"all", "active", "inactive"}
_VALID_MEDIA_TYPES = {"all", "image", "video", "meme"}


class ParsingConfigIn(BaseModel):
    keyword: str | None = None
    country: str
    vertical: str = "nutra"
    is_active: bool = True
    notes: str | None = None
    partner: str | None = None
    category: str | None = None
    languages: list[str] | None = None
    config_type: str = "keyword"
    active_status: str | None = None
    media_type_filter: str | None = None
    platforms: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    advertiser: str | None = None
    auto_date_from_last_parse: bool = False

    @field_validator("config_type")
    @classmethod
    def validate_config_type(cls, v: str) -> str:
        if v not in _VALID_CONFIG_TYPES:
            raise ValueError(f"config_type must be one of: {_VALID_CONFIG_TYPES}")
        return v

    @field_validator("active_status")
    @classmethod
    def validate_active_status(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_ACTIVE_STATUS:
            raise ValueError(f"active_status must be one of: {_VALID_ACTIVE_STATUS}")
        return v

    @field_validator("media_type_filter")
    @classmethod
    def validate_media_type_filter(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_MEDIA_TYPES:
            raise ValueError(f"media_type_filter must be one of: {_VALID_MEDIA_TYPES}")
        return v


ParsingConfigCreate = ParsingConfigIn


class ParsingConfigUpdate(BaseModel):
    keyword: str | None = None
    country: str | None = None
    vertical: str | None = None
    is_active: bool | None = None
    notes: str | None = None
    partner: str | None = None
    category: str | None = None
    languages: list[str] | None = None
    config_type: str | None = None
    active_status: str | None = None
    media_type_filter: str | None = None
    platforms: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    advertiser: str | None = None
    auto_date_from_last_parse: bool | None = None

    @field_validator("config_type")
    @classmethod
    def validate_config_type(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_CONFIG_TYPES:
            raise ValueError(f"config_type must be one of: {_VALID_CONFIG_TYPES}")
        return v

    @field_validator("active_status")
    @classmethod
    def validate_active_status(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_ACTIVE_STATUS:
            raise ValueError(f"active_status must be one of: {_VALID_ACTIVE_STATUS}")
        return v

    @field_validator("media_type_filter")
    @classmethod
    def validate_media_type_filter(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_MEDIA_TYPES:
            raise ValueError(f"media_type_filter must be one of: {_VALID_MEDIA_TYPES}")
        return v


class ParsingConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    keyword: str | None
    country: str
    vertical: str
    is_active: bool
    notes: str | None
    partner: str | None = None
    category: str | None = None
    languages: list[str] | None = None
    config_type: str = "keyword"
    active_status: str | None = None
    media_type_filter: str | None = None
    platforms: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    advertiser: str | None = None
    auto_date_from_last_parse: bool = False
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
    reach: int | None = None
    reach_breakdown: dict | None = None
    eu_countries: list[str] | None = None
    used_in_ads_count: int | None = None
    spend_estimate: int | None = None
    partner: str | None = None
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