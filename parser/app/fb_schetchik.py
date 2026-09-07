"""Снятие счётчика результатов FB («~3,300 results») со страницы Ad Library.

Зачем: витрина показывает объявления БРЕНДА, а FB — совпадения по подстроке со
стеммингом. По «ProstaMen» в Польше FB рисует ~3 300, и это польское слово
«prosta» (простой): L'Oréal, шампуни, школа польского. Реальных объявлений бренда
там полсотни. Без этой цифры рядом разрыв выглядит недосбором и всплывает
вопросом от заказчика.

В ответе GraphQL числа нет — оно есть только в разметке страницы, поэтому берём
браузером и храним снимком в parsing_configs.fb_schetchik.
"""
import asyncio
import re

from loguru import logger

from app.browser import browser_context, build_library_url, goto_with_challenge_retry
from app.db import AsyncSessionLocal
from sqlalchemy import text as _sa_text

# «~3,300 results» / «Około 3 300 wyników» / «~3 300 результатов»
_RE_SCHET = re.compile(
    r"[~≈]?\s*([\d][\d\s., ]{0,14})\s*(?:results|wynik\w*|результат\w*)",
    re.IGNORECASE,
)

# Одновременных браузеров: страница тяжёлая, а конфигов под полторы сотни.
_PARALLELNO = 4
# Сколько ждать прорисовки счётчика после загрузки.
_ZHDAT_MS = 9000


def _razobrat(text: str) -> int | None:
    m = _RE_SCHET.search(text or "")
    if not m:
        return None
    chistoe = re.sub(r"[^\d]", "", m.group(1))
    if not chistoe:
        return None
    try:
        return int(chistoe)
    except ValueError:
        return None


async def snyat_odin(country: str, keyword: str | None,
                     is_targeted_country: bool | None = None) -> int | None:
    """Счётчик FB для одного ключа. None — не удалось снять (не ноль!).

    Отличать «не удалось» от «ноль» обязательно: иначе сбой сети запишется в базу
    как «у FB ничего нет» и будет выглядеть правдой.
    """
    if not keyword:
        return None
    url = build_library_url(
        country=country, keyword=keyword, languages=None,
        active_status="all", media_type="all",
        sort_mode="total_impressions", sort_direction="desc",
        is_targeted_country=is_targeted_country,
    )
    try:
        async with browser_context(block_resources=True) as ctx:
            page = await ctx.new_page()
            ok = await goto_with_challenge_retry(page, url)
            if not ok:
                return None
            await page.wait_for_timeout(_ZHDAT_MS)
            body = await page.inner_text("body")
            return _razobrat(body)
    except Exception as exc:
        logger.warning(f"[счётчик] {keyword}/{country}: не снял ({type(exc).__name__}: {exc})")
        return None


async def obnovit_schetchiki(config_ids: list[int] | None = None) -> dict:
    """Обновить счётчики FB для активных keyword-конфигов.

    Вызывается в конце прогона. Никогда не роняет прогон: любая ошибка — просто
    пропущенный конфиг, старое значение остаётся с прежней отметкой времени.
    """
    async with AsyncSessionLocal() as session:
        sql = ("SELECT id, keyword, country, is_targeted_country FROM parsing_configs "
               "WHERE is_active AND config_type = 'keyword' AND keyword IS NOT NULL")
        params: dict = {}
        if config_ids:
            sql += " AND id = ANY(:ids)"
            params["ids"] = list(config_ids)
        rows = (await session.execute(_sa_text(sql), params)).all()

    itogo = {"vsego": len(rows), "snyato": 0, "ne_udalos": 0}
    sem = asyncio.Semaphore(_PARALLELNO)

    async def _odin(cid, kw, country, targeted):
        async with sem:
            n = await snyat_odin(country, kw, targeted)
        if n is None:
            itogo["ne_udalos"] += 1
            return
        itogo["snyato"] += 1
        try:
            async with AsyncSessionLocal() as s:
                await s.execute(_sa_text(
                    "UPDATE parsing_configs SET fb_schetchik = :n, fb_schetchik_at = now() "
                    "WHERE id = :id"), {"n": n, "id": cid})
                await s.commit()
        except Exception as exc:
            logger.warning(f"[счётчик] не записал #{cid}: {exc}")

    await asyncio.gather(*(_odin(*r) for r in rows), return_exceptions=True)
    logger.info(
        f"[счётчик] обновлено {itogo['snyato']} из {itogo['vsego']}, "
        f"не удалось {itogo['ne_udalos']}"
    )
    return itogo
