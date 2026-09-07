"""Проверка: пул языков из карточки гео управляет планом (сеткой и родными словами)."""
import asyncio
from types import SimpleNamespace

from app.db import AsyncSessionLocal
from app import coordinator as C


async def run():
    async with AsyncSessionLocal() as s:
        slovar = []          # словарь тут не важен, смотрим только языковую часть
        for pool in (None, ["es", "zh"], ["ar"]):
            cfg = SimpleNamespace(country="US", languages=pool)
            yazyki = C._yazyki_geo(cfg)
            rodnye = await C._rodnye_slova(s, yazyki)
            plan = C._plan_dnya(cfg, {}, slovar, rodnye)
            setka = sum(1 for x in plan if x.get("languages") and not x.get("keyword"))
            nat = sum(1 for x in plan if x.get("languages") and x.get("keyword"))
            vidno = sorted({x["languages"][0] for x in plan if x.get("languages")})
            print(f"пул {str(pool):<14} → языков в плане {len(yazyki):>2}, "
                  f"срезов сетки {setka:>3}, родных слов {nat:>4}, языки в плане: {vidno[:6]}"
                  f"{' …' if len(vidno) > 6 else ''}")

    print("\nожидаем: при заданном пуле в плане ровно эти языки и только их родные слова")

asyncio.run(run())
