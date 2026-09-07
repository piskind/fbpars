"""Проверка постановки срезов: гоняем enqueue_run с ПОДМЕНЁННОЙ очередью.

Очередь подменяется заглушкой, поэтому в Redis ничего не улетает — проверяем только то,
что координатор построил и какие kwargs уходят в джобу.
"""
import asyncio
from collections import Counter

from app.db import AsyncSessionLocal
from app.models import ParserRun
from app import coordinator as C


class FakeJob:
    @staticmethod
    def exists(jid, connection=None):
        return False


class FakeQueue:
    def __init__(self):
        self.postavleno = []
        self.connection = None

    def enqueue(self, func, kwargs=None, job_id=None, **_):
        self.postavleno.append((job_id, kwargs))


async def run():
    # тестовый прогон: адресный, только конфиг 331
    async with AsyncSessionLocal() as s:
        run_row = ParserRun(status="triggered", mode="filters", only_configs="331")
        s.add(run_row)
        await s.commit()
        await s.refresh(run_row)
        run_id = run_row.id
    print(f"тестовый прогон #{run_id} (адресный, конфиг 331)")

    fake = FakeQueue()
    import app.queue as Q
    import rq.job as RJ
    staryy_q, staryy_job = Q.parse_queue, RJ.Job
    Q.parse_queue = lambda: fake
    RJ.Job = FakeJob
    try:
        job_ids = await C.enqueue_run(run_id)
    finally:
        Q.parse_queue, RJ.Job = staryy_q, staryy_job
        async with AsyncSessionLocal() as s:
            row = await s.get(ParserRun, run_id)
            if row:
                await s.delete(row)
                await s.commit()
        print(f"тестовый прогон #{run_id} удалён")

    print(f"\nпоставлено бы срезов: {len(job_ids)}")
    vidy = Counter()
    for jid, kw in fake.postavleno:
        if kw.get("languages"):
            vidy["язык × медиа"] += 1
        elif kw.get("keyword"):
            vidy["слово" + (" × медиа" if kw.get("media_type") else " (разведка)")] += 1
        else:
            vidy["базовая сетка"] += 1
    for k, v in vidy.most_common():
        print(f"  {k:<22}: {v}")

    print("\nпримеры kwargs:")
    for jid, kw in (fake.postavleno[0], fake.postavleno[10], fake.postavleno[200],
                    fake.postavleno[-1]):
        kratko = {k: v for k, v in kw.items() if k != "date_from"}
        print(f"  {jid}\n    {kratko}")

    ids = [j for j, _ in fake.postavleno]
    assert len(ids) == len(set(ids)), "job_id обязаны быть уникальны"
    assert all(kw.get("only_day") for _, kw in fake.postavleno), "у каждого среза должен быть only_day"
    assert all(kw.get("date_to") for _, kw in fake.postavleno), "у каждого среза должна быть отсечка"
    assert all(kw.get("run_id") == run_id for _, kw in fake.postavleno), "run_id должен проставляться"
    assert all("tag" not in kw for _, kw in fake.postavleno), "tag не должен уезжать в parse_chunk"
    print("\nпроверки постановки ОК")

asyncio.run(run())
