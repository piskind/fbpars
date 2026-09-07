"""Дымовой тест выключателя полосы: мёртвый канал должен уводить трафик в пул."""
import asyncio
import os

os.environ["PROXY_MODE"] = "hybrid"
os.environ["HYBRID_GOST_SHARE"] = "1.0"   # обычно ВЕСЬ трафик в мобильный канал
os.environ["LANE_FAIL_LIMIT"] = "3"
os.environ["LANE_DEAD_SEC"] = "60"

import importlib
import app.config
importlib.reload(app.config)
import app.proxy
importlib.reload(app.proxy)


async def main():
    p = app.proxy.get_provider()
    gw = app.proxy.worker_gateway()

    before = [await p.get_proxy_url() for _ in range(6)]
    assert all(u == gw for u in before), "до сбоев весь трафик должен идти в мобильный канал"
    print(f"до сбоев: {sum(1 for u in before if u == gw)}/6 через мобильный канал")

    for _ in range(3):
        p.report_transport_error(gw)

    after = [await p.get_proxy_url() for _ in range(6)]
    gost_after = sum(1 for u in after if u == gw)
    print(f"после 3 сбоев подряд: {gost_after}/6 через мобильный канал (ожидаем 0)")
    assert gost_after == 0, "полоса должна быть отключена"

    p.report_success(gw)
    back = [await p.get_proxy_url() for _ in range(6)]
    print(f"после успешного ответа: {sum(1 for u in back if u == gw)}/6 через мобильный канал (ожидаем 6)")
    assert all(u == gw for u in back), "успех должен вернуть полосу в работу"
    print("выключатель полосы ОК")

asyncio.run(main())
