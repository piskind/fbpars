from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The old weak fallback that used to be the default — now explicitly rejected so a
# forgotten JWT_SECRET can never silently sign tokens with a value that's in the repo.
_WEAK_JWT_SECRETS = {"", "change-me-in-prod-very-long-random-string"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    s3_endpoint_url: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    s3_region: str = "ru-3"

    # No default: JWT_SECRET MUST come from .env. A missing/weak value now fails startup
    # loudly instead of falling back to a public repo string that lets anyone forge tokens.
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24

    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    resend_api_key: str = ""
    frontend_base_url: str = "http://localhost:5173"
    debug: bool = False

    @field_validator("jwt_secret")
    @classmethod
    def _reject_weak_jwt_secret(cls, v: str) -> str:
        if v.strip() in _WEAK_JWT_SECRETS:
            raise ValueError(
                "JWT_SECRET is unset or still the public placeholder — set a long random "
                "value in .env (e.g. `openssl rand -hex 32`) before starting the API."
            )
        return v


settings = Settings()
