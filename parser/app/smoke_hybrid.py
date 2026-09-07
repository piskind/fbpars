"""Дымовой тест гибридного провайдера: распределение полос и живой запрос по каждой."""
import asyncio
import os

os.environ["PROXY_MODE"] = "hybrid"

import importlib
import app.config
importlib.reload(app.config)
import app.proxy
importlib.reload(app.proxy)


async def main():
    p = app.proxy.get_provider()
    urls = [await p.get_proxy_url() for _ in range(40)]
    gost = sum(1 for u in urls if u and "gost" in u)
    print(f"провайдер={type(p).__name__}: из 40 запросов gost={gost}, пул={40 - gost}")
    assert 20 <= gost <= 38, "доля мобильного канала вне ожидаемого диапазона"
    print("распределение полос ОК")

asyncio.run(main())
