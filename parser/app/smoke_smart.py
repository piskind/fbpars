"""Дымовой тест умного плана: что построится для гео с историей и для нового гео."""
import asyncio
from collections import Counter

from app.db import AsyncSessionLocal
from app.models import ParsingConfig
from app import coordinator as C


def razbor(plan):
    vidy = Counter()
    kapy = Counter()
    for s in plan:
        if s.get("languages") and s.get("keyword"):
            vidy["родное слово × язык"] += 1
        elif s.get("languages"):
            vidy["язык × медиа"] += 1
        elif s.get("keyword") and s["tag"].startswith("razv-"):
            vidy["разведка (новые слова)"] += 1
        elif s.get("keyword"):
            vidy["слово × медиа"] += 1
        else:
            vidy["базовая сетка"] += 1
        kapy[s["max_ads"]] += 1
    return vidy, kapy


async def run():
    async with AsyncSessionLocal() as s:
        cfg = await s.get(ParsingConfig, 331)
        slovar = C._proven_words()
        istoriya = await C._slova_geo(s, cfg.country)
        rodnye = await C._rodnye_slova(s, C._yazyki_geo(cfg))
        plan = C._plan_dnya(cfg, istoriya, slovar, rodnye)
        print(f"=== {cfg.country} (есть история) ===")
        print(f"словарь на диске: {len(slovar)}, слов с отдачей >= {C._SWEEP_WORD_MIN}: {len(istoriya)}, "
              f"родных слов {sum(len(v) for v in rodnye.values())} по {len(rodnye)} языкам")
        vidy, kapy = razbor(plan)
        for k, v in vidy.items():
            print(f"  {k:<26}: {v}")
        print(f"  ИТОГО срезов: {len(plan)}   (потолки: {dict(kapy)})")

        # то же гео, но как будто истории нет
        plan2 = C._plan_dnya(cfg, {}, slovar, rodnye)
        vidy2, _ = razbor(plan2)
        print(f"\n=== новое гео (истории нет) ===")
        for k, v in vidy2.items():
            print(f"  {k:<26}: {v}")
        print(f"  ИТОГО срезов: {len(plan2)}")

        print(f"\nбыло в старом плане по CA: 80 318 срезов")
        print(f"стало: {len(plan)} → в {80318 / max(len(plan), 1):.1f} раза меньше работы")
        # проверки
        assert all("only_day" not in x for x in plan), "only_day навешивает enqueue_run, не план"
        assert plan[0]["max_ads"] == C._DAY_SWEEP_CAP, "первой идёт базовая сетка"
        assert any(x.get("languages") for x in plan), "языковая сетка должна быть в плане"
        tags = [x["tag"] for x in plan]
        assert len(tags) == len(set(tags)), "теги срезов должны быть уникальны (иначе job_id совпадут)"
        print("проверки плана ОК")

asyncio.run(run())
