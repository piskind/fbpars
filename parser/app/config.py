from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    proxy_http_gateway: str = "http://gost:8888"
    proxy_rotate_url: str
    proxy_rotate_wait_sec: int = 20

    s3_endpoint_url: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    s3_region: str = "ru-1"

    log_level: str = "INFO"
    headless: bool = True
    scroll_max_attempts: int = 50
    scroll_pause_sec: int = 2
    use_graphql: bool = True
    # "fetch"    → in-page fetch() pagination, no scroll (flat RAM, no ~1700 cap)
    # "intercept" → scroll + page.on("requestfinished") response interception (RAM grows)
    graphql_mode: str = "fetch"


settings = Settings()