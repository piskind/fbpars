import asyncio
from sqlalchemy import select
from app.db import AsyncSessionLocal
from app.models import ParsingConfig, Proxy
from app.config import settings
from loguru import logger


OFFERS = [
    ("OXYS", ["MX", "PE", "EC"]),
    ("Sustarox", ["PE", "EC"]),
    ("Audix", ["PE"]),
    ("Tridentex", ["PY"]),
    ("Retime Serum", ["MX"]),
    ("Joint Flexi", ["EG"]),
]

KEYWORDS = [
    ("diabet", ["MX"]),
    ("sangre", ["PE"]),
    ("hipertensión", ["EC"]),
    ("articulación", ["PE"]),
    ("piel", ["PY"]),
    ("adelgazamiento", ["PE"]),
    ("kg", ["MX"]),
]


async def seed_configs(session) -> int:
    added = 0
    for keyword, countries in OFFERS + KEYWORDS:
        for country in countries:
            stmt = select(ParsingConfig).where(
                ParsingConfig.keyword == keyword,
                ParsingConfig.country == country,
            )
            exists = (await session.execute(stmt)).scalar_one_or_none()
            if exists:
                continue
            session.add(ParsingConfig(keyword=keyword, country=country, is_active=True))
            added += 1
    await session.commit()
    return added


async def seed_proxies(session) -> int:
    stmt = select(Proxy).where(Proxy.label == "fxdx-mobile-us-1")
    exists = (await session.execute(stmt)).scalar_one_or_none()
    if exists:
        return 0

    proxy = Proxy(
        label="fxdx-mobile-us-1",
        proxy_type="socks5",
        host="q2pg3fdipz.cn.fxdx.in",
        port=15690,
        username="bmusproxy154342",
        password="b3bdfbhiaeac",
        rotate_url="https://i.fxdx.in/actionlinks/do/changeip/z7b2Rg6yRp2DhKLuCtqs4g",
        country=None,
        is_active=True,
    )
    session.add(proxy)
    await session.commit()
    return 1


async def main():
    async with AsyncSessionLocal() as session:
        configs_added = await seed_configs(session)
        proxies_added = await seed_proxies(session)
        logger.info(f"Seeded {configs_added} configs, {proxies_added} proxies")


if __name__ == "__main__":
    asyncio.run(main())