from .config import settings


def get_proxy() -> dict | None:
    if not settings.proxy_url:
        return None
    return {"server": settings.proxy_url}
