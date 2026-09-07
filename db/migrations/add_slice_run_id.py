"""Идемпотентно: slice_events.run_id.

Без него сводка привязывает срезы к прогону ПО ВРЕМЕНИ, и срез, начатый в одном
прогоне, а закрывшийся в окне следующего, приписывается чужому: DiaStop/UZ из
прогона 319 попал в отчёт 320 с 8 627 карточками. Для отчёта, на который смотрят
перед разговором с заказчиком, это недопустимо. Безопасно перезапускать.
"""
import asyncpg, asyncio, os


async def migrate():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        exists = await conn.fetchval("SELECT to_regclass('public.slice_events');")
        if not exists:
            print("Migration SKIP: slice_events table not present yet")
            return
        await conn.execute("ALTER TABLE slice_events ADD COLUMN IF NOT EXISTS run_id INTEGER;")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_slice_events_run ON slice_events (run_id);")
        print("Migration OK: slice_events.run_id added")
    finally:
        await conn.close()


asyncio.run(migrate())
