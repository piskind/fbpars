"""Идемпотентно: parsing_configs.fb_schetchik / fb_schetchik_at.

Сколько результатов показывает сам FB по этому ключу («~3,300 results» на странице
Ad Library). Нужно, чтобы разрыв между витриной и FB был виден и объясним, а не
всплывал вопросом от заказчика: FB ищет со стеммингом, и по «ProstaMen» его 3 300
это польское слово «prosta», а не объявления бренда (их полсотни). В ответе
GraphQL этого числа нет — оно есть только на самой странице, поэтому храним снимок
и время снятия. Безопасно перезапускать.
"""
import asyncpg, asyncio, os


async def migrate():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        exists = await conn.fetchval("SELECT to_regclass('public.parsing_configs');")
        if not exists:
            print("Migration SKIP: parsing_configs table not present yet")
            return
        await conn.execute(
            "ALTER TABLE parsing_configs ADD COLUMN IF NOT EXISTS fb_schetchik INTEGER;")
        await conn.execute(
            "ALTER TABLE parsing_configs ADD COLUMN IF NOT EXISTS fb_schetchik_at TIMESTAMPTZ;")
        print("Migration OK: parsing_configs.fb_schetchik/fb_schetchik_at added")
    finally:
        await conn.close()


asyncio.run(migrate())
