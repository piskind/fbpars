from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    s3_endpoint_url: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    s3_region: str = "ru-3"

    jwt_secret: str = "change-me-in-prod-very-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24

    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    resend_api_key: str = ""
    frontend_base_url: str = "http://localhost:5173"
    debug: bool = False


settings = Settings()
