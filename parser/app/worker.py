import asyncio
import os as _os
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import select, func, text as _sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.models import Ad, Creative
from loguru import logger
from app.db import AsyncSessionLocal
from app.models import ParsingConfig, AdMediaType, ChunkProgress, chunk_key
from app.browser import (
    browser_context,
    build_library_url,
    goto_with_challenge_retry,
    scrape_via_browser_graphql,
    scrape_via_page_fetch,
)
from app.parsers.library_card import parse_card_text
from app.parsers.library_extractor import scroll_and_collect
from app.graphql_client import map_graphql_card
import time
from sqlalchemy import text
import hashlib as _hashlib

from app.graphql_paginator import paginate, EmptyNoPagination
from app.repository import upsert_ad, save_creative
from app.storage import MediaUploader
from app.proxy import rotate_ip, current_ip
from app.config import settings

import re as _re
import unicodedata as _ud
# Всё, кроме букв и цифр: и ключ, и текст объявления приводим к одному виду,
# чтобы «Bio prost» находился в bioprost.com.
_re_glasnye = _re.compile(r"[aeiouyаеёиоуыэюя]", _re.IGNORECASE)
# Оставляем буквы и цифры ЛЮБОГО алфавита, а не только латиницу с кириллицей.
# Старый класс стирал арабский, греческий, тайский и иероглифы в пустоту, из-за чего:
#   • объявление на местном языке проходило отсев только если бренд стоял латиницей
#     в домене — в EG, GR, CY, TN мы видели лишь часть рекламы;
#   • НЕЛАТИНСКИЙ КЛЮЧ молча ВЫКЛЮЧАЛ отсев целиком (ключ сворачивался в пустую
#     строку), и такой конфиг собрал бы всю выдачу FB без фильтрации.
_re_otsev = _re.compile(r"[\W_]+", _re.UNICODE)


def _svernut(text: str) -> str:
    """Привести текст к виду для сравнения с ключом: сложить диакритику и
    выбросить всё, кроме букв и цифр.

    Складывать диакритику обязательно: _re_otsev не знает «á» и выбрасывает её
    целиком, из-за чего «Bururán» в тексте превращался в «bururn» и ключ
    «Bururan» в нём не находился — живая реклама бренда уходила в отсев.
    """
    return _re_otsev.sub("", "".join(
        c for c in _ud.normalize("NFKD", text.lower()) if not _ud.combining(c)
    ))

# Отсеивать ли на сборе по ключу карточки, где ключа нет в тексте.
_OTSEV_PO_KLYUCHU = (_os.getenv("OTSEV_PO_KLYUCHU", "true") or "true").lower() not in ("0", "false", "no")

# Shared across all concurrent configs if run_once ever parallelises process_config calls.
# Currently one config runs at a time, so this is effectively per-config.
MEDIA_SEMAPHORE = asyncio.Semaphore(10)

# Сколько пачек подряд должны целиком промахнуться мимо целевого дня, чтобы закрыть срез.
# 1 — прежнее поведение; 2 даёт 400 карточек терпимости к ранжированию FB.
_DAY_DONE_PACHEK = int(_os.getenv("DAY_DONE_PACHEK", "2") or 2)


async def get_active_configs(
    session, config_id: int | None = None, mode: str | None = None
) -> list[ParsingConfig]:
    if config_id is not None:
        cfg = await session.get(ParsingConfig, config_id)
        return [cfg] if cfg else []
    stmt = select(ParsingConfig).where(ParsingConfig.is_active.is_(True))
    # Режим рана ограничивает набор конфигов по типу: keyword / filters(+fanpage) / all.
    if mode == "keyword":
        stmt = stmt.where(ParsingConfig.config_type == "keyword")
    elif mode == "filters":
        stmt = stmt.where(ParsingConfig.config_type.in_(["filters", "fanpage"]))
    stmt = stmt.order_by(ParsingConfig.id)
    return list((await session.execute(stmt)).scalars().all())


async def _upload_card_media(
    uploader: MediaUploader,
    config_id: int,
    ad_id: int,
    card,
    country: str | None = None,
) -> dict:
    s = {"media_ok": 0, "media_fail": 0, "removed_no_media": 0, "skipped_phash_duplicate": 0}
    all_reused = True
    img_uploads: list[dict] = []
    vid_uploads: list[dict] = []
    fail_count = 0

    async with MEDIA_SEMAPHORE:
        for idx, img_url in enumerate(card.image_urls[:3]):
            upload = await uploader.upload_image(card.library_id, img_url, idx, country)
            if upload:
                img_uploads.append(upload)
            else:
                fail_count += 1

        # Video is the heaviest traffic and the server has a transfer cap — skip it on the
        # first pass by default (SKIP_VIDEO_FIRST_PASS). Poster still represents the ad.
        if settings.skip_video_first_pass:
            if card.video_urls:
                s["video_skipped"] = len(card.video_urls[:2])
        else:
            for idx, vid_url in enumerate(card.video_urls[:2]):
                upload = await uploader.upload_video(card.library_id, vid_url, idx)
                if upload:
                    vid_uploads.append(upload)
                else:
                    fail_count += 1

        # Fall back to poster images when there are no images AND we didn't just save videos.
        if not card.image_urls and not vid_uploads:
            for idx, poster_url in enumerate(card.poster_urls[:2]):
                upload = await uploader.upload_image(card.library_id, poster_url, idx, country)
                if upload:
                    img_uploads.append(upload)
                else:
                    fail_count += 1

    s["media_fail"] = fail_count
    media_saved = len(img_uploads) + len(vid_uploads)
    s["media_ok"] = media_saved

    # Track real downloaded traffic (phash-reused uploads cost nothing) — server has a cap.
    downloaded_bytes = sum(
        u.get("file_size") or 0
        for u in (img_uploads + vid_uploads)
        if not u.get("reused")
    )
    s["bytes_downloaded"] = downloaded_bytes
    if downloaded_bytes:
        logger.info(f"[#{config_id}] {card.library_id}: downloaded {downloaded_bytes / 1_048_576:.1f} MB")

    for upload in img_uploads:
        if not upload.get("reused"):
            all_reused = False
    if vid_uploads:
        all_reused = False  # videos are never reused

    async with AsyncSessionLocal() as session:
        for upload in img_uploads:
            await save_creative(session, ad_id, AdMediaType.IMAGE, upload)
        for upload in vid_uploads:
            await save_creative(session, ad_id, AdMediaType.VIDEO, upload)
        await session.flush()

        if all_reused and media_saved > 0:
            # All creatives reuse existing S3 keys — no new uploads, but keep the ad record.
            # A new library_id is still a distinct ad even if visuals are identical.
            s["skipped_phash_duplicate"] += 1
            logger.info(f"[#{config_id}] phash-reuse {card.library_id}: saved with existing S3 keys")

        await session.commit()

        cnt = await session.scalar(
            select(func.count(Creative.id))
            .where(Creative.ad_id == ad_id, Creative.s3_url.is_not(None))
        )
        if not cnt:
            db_ad = await session.get(Ad, ad_id)
            if db_ad:
                await session.delete(db_ad)
                await session.commit()
                logger.info(f"[#{config_id}] removed {card.library_id}: no media saved")
                s["removed_no_media"] += 1

    return s


def split_date_range(date_from: date, date_to: date, chunk_days: int = 1) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    current = date_from
    while current <= date_to:
        chunk_end = min(current + timedelta(days=chunk_days - 1), date_to)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


class _DayDone(Exception):
    """Выдача ушла раньше целевого дня — листать дальше нечего."""


_EMPTY_STATS = {
    "raw": 0, "new": 0, "updated": 0, "urls_saved": 0, "media_ok": 0, "media_fail": 0,
    "errors": 0, "skipped_duplicate": 0, "skipped_no_media": 0,
    "skipped_already_reviewed": 0, "skipped_phash_duplicate": 0, "removed_no_media": 0,
}


# Кириллица → латиница для сравнения с ДОМЕНОМ посадочной. Русскоязычная нутра
# почти всегда льёт на транслитерированный домен (silapchely.kg, sustaflex.uz),
# а отсев искал только кириллицу — и «Сила пчелы» по KG/AM/GE отдала 860 карточек,
# из которых в базу не попало НИ ОДНОЙ. Даём по два варианта на спорные буквы:
# однозначной нормы транслита нет, пишут и «y», и «i», и «ju», и «yu».
_TRANSLIT = {
    "а": ("a",), "б": ("b",), "в": ("v",), "г": ("g",), "д": ("d",), "е": ("e",),
    "ё": ("e", "yo"), "ж": ("zh", "j"), "з": ("z",), "и": ("i",), "й": ("y", "i"),
    "к": ("k",), "л": ("l",), "м": ("m",), "н": ("n",), "о": ("o",), "п": ("p",),
    "р": ("r",), "с": ("s",), "т": ("t",), "у": ("u",), "ф": ("f",), "х": ("h", "kh"),
    "ц": ("c", "ts"), "ч": ("ch",), "ш": ("sh",), "щ": ("sch", "shch"), "ъ": ("",),
    "ы": ("y", "i"), "ь": ("",), "э": ("e",), "ю": ("yu", "ju"), "я": ("ya", "ja"),
}


def _translit(slovo: str, predel: int = 8) -> list[str]:
    """Варианты латинской записи кириллического слова (не больше predel штук)."""
    varianty = [""]
    for ch in slovo:
        zameny = _TRANSLIT.get(ch, (ch,))
        novye = []
        for v in varianty:
            for z in zameny:
                novye.append(v + z)
                if len(novye) >= predel:
                    break
            if len(novye) >= predel:
                break
        varianty = novye
    return [v for v in dict.fromkeys(varianty) if v]


def _klyuch_dlya_otseva(config, kw_override: str | None = None):
    """Слово и скелет для отсева — или (None, None), если отсев не применяется.

    Отсев только для конфигов типа keyword: там ключ означает «объявления про это»,
    а у filters он лишь способ обойти выдачу.
    """
    if not (_OTSEV_PO_KLYUCHU and getattr(config, "config_type", "") == "keyword"):
        return None, None
    # Слово среза, а не config.keyword: у filters-конфигов он пуст.
    _kl = _svernut((kw_override or getattr(config, "keyword", "") or "").strip())
    if not _kl:
        return None, None
    # Скелет из согласных — запасное сравнение ТОЛЬКО по ссылке, порог пять знаков.
    _sk = _re_glasnye.sub("", _kl)
    return _kl, (_sk if len(_sk) >= 5 else None)


def _latinskie_varianty(slovo: str) -> list[str]:
    """Латинские написания кириллического ключа — искать в домене посадочной.

    Короче пяти знаков не берём: «Мёд» → «med» совпало бы с любым medical.
    """
    if not slovo or not any("а" <= c <= "я" or c == "ё" for c in slovo):
        return []
    return [v for v in _translit(slovo) if len(v) >= 5]


def _karta_po_klyuchu(card, slovo: str, skelet: str | None) -> bool:
    """Встречается ли ключ в объявлении.

    ОДНО место на оба пути сбора: GraphQL-пагинацию и DOM-fallback. Раньше отсев
    жил только в _persist_batch, и срез, ушедший в DOM-fallback, сохранял выдачу
    целиком: #119 Flexosamine ES так занёс 231 объявление, из них 179 не по ключу.
    """
    _gde_iskat = _svernut(" ".join(filter(None, (
        card.body_text, card.title, card.caption,
        getattr(card, "page_name", None),
        getattr(card, "link_url", None),
        getattr(card, "display_url", None),
    ))))
    if slovo in _gde_iskat:
        return True
    _ssylka = None
    if skelet:
        # Сокращённая запись без гласных живёт в ССЫЛКЕ (ARTrnL32ES — это Artronol),
        # а не в тексте: по тексту скелет ловил чужую рекламу тысячами.
        _ssylka = _svernut(" ".join(filter(None, (
            getattr(card, "link_url", None),
            getattr(card, "display_url", None),
        ))))
        if skelet in _re_glasnye.sub("", _ssylka):
            return True
    # Кириллический бренд в латинском домене: silapchely.kg для «Сила пчелы».
    _lat = _latinskie_varianty(slovo)
    if _lat:
        if _ssylka is None:
            _ssylka = _svernut(" ".join(filter(None, (
                getattr(card, "link_url", None),
                getattr(card, "display_url", None),
            ))))
        if any(v in _ssylka for v in _lat):
            return True
    return False


async def _upsert_cards_and_collect_media(
    cards,
    config: ParsingConfig,
    period_tag: str,
) -> tuple[dict, list]:
    """Phase 1: sequential DB upserts. Returns (stats_delta, media_tasks)."""
    stats = dict(_EMPTY_STATS)
    seen_ids: set[str] = set()
    media_tasks: list[tuple[int, object]] = []
    _slovo, _skelet = _klyuch_dlya_otseva(config)

    for card in cards:
        if not card.library_id:
            continue
        if card.library_id in seen_ids:
            stats["skipped_duplicate"] += 1
            continue
        seen_ids.add(card.library_id)

        if not card.image_urls and not card.video_urls and not card.poster_urls:
            logger.info(f"[#{config.id}]{period_tag} skip {card.library_id}: no media")
            stats["skipped_no_media"] += 1
            continue

        # Сбор по ключу: слово должно реально встречаться в объявлении. Тот же
        # отсев, что и на GraphQL-пути — иначе DOM-fallback заносит выдачу целиком.
        if _slovo and not _karta_po_klyuchu(card, _slovo, _skelet):
            stats["ne_po_klyuchu"] = stats.get("ne_po_klyuchu", 0) + 1
            continue

        async with AsyncSessionLocal() as session:
            try:
                ad, is_new, skipped = await upsert_ad(
                    session, card, config.country, config.keyword, config.vertical, config.config_type
                )
                await session.commit()
            except Exception as e:
                logger.warning(f"[#{config.id}]{period_tag} DB error for {card.library_id}: {e}")
                await session.rollback()
                stats["errors"] += 1
                continue

        if is_new:
            stats["new"] += 1
        elif skipped:
            stats["skipped_already_reviewed"] += 1
        else:
            stats["updated"] += 1

        if is_new and not skipped:
            # Direct FB CDN URLs are already persisted by upsert_ad — nothing to download.
            if card.image_urls or card.video_urls:
                stats["urls_saved"] += 1
            # Legacy S3 download/phash pipeline runs only when explicitly re-enabled.
            if settings.enable_media_download:
                media_tasks.append((ad.id, card))

    return stats, media_tasks


async def _run_media_phase(
    media_tasks,
    config: ParsingConfig,
    uploader: MediaUploader,
    period_tag: str,
) -> dict:
    """Phase 2: parallel media uploads. Returns stats_delta."""
    stats = {}
    logger.info(f"[#{config.id}]{period_tag} starting parallel media for {len(media_tasks)} new ads")
    results = await asyncio.gather(
        *[_upload_card_media(uploader, config.id, ad_id, card, config.country) for ad_id, card in media_tasks],
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"[#{config.id}]{period_tag} media task error: {r}")
            stats["errors"] = stats.get("errors", 0) + 1
        else:
            for k, v in r.items():
                stats[k] = stats.get(k, 0) + v
    return stats


async def _dispatch_media(
    media_tasks,
    config: ParsingConfig,
    uploader: MediaUploader,
    period_tag: str,
) -> dict:
    """Phase 4: either enqueue one media job per ad (split pipeline) or upload inline.

    In the split path, metadata is already committed; media (image + phash-dedup, video
    deferred by SKIP_VIDEO_FIRST_PASS) runs on the dedicated media queue / media-worker.
    """
    if not media_tasks:
        return {}

    if settings.split_media_pipeline and settings.use_queue:
        from app.queue import media_queue
        from app.tasks import process_media
        from rq.job import Job

        q = media_queue()
        conn = q.connection
        enqueued = 0
        for ad_id, card in media_tasks:
            jid = f"media_{ad_id}"  # colon-free RQ job id (see coordinator.enqueue_run note)
            if Job.exists(jid, connection=conn):
                continue
            q.enqueue(
                process_media,
                kwargs={
                    "ad_id": ad_id,
                    "library_id": card.library_id,
                    "image_urls": list(card.image_urls or []),
                    "video_urls": list(card.video_urls or []),
                    "poster_urls": list(card.poster_urls or []),
                    "config_id": config.id,
                },
                job_id=jid,
                job_timeout=settings.media_job_timeout,
                result_ttl=1800,
                failure_ttl=86400,
            )
            enqueued += 1
        logger.info(f"[#{config.id}]{period_tag} enqueued {enqueued} media job(s)")
        return {"media_enqueued": enqueued}

    return await _run_media_phase(media_tasks, config, uploader, period_tag)



async def _preload_seen_ids(country: str) -> set[str]:
    """Library IDs already saved for this COUNTRY, seeded into the paginator's dedup set so a
    resumed/drifted cursor doesn't re-process cards already in the DB.

    Scope MUST match what upsert_ad actually dedups on — the (library_id, country) unique key,
    with NO date filter. The old scope filtered by `Ad.started_at` inside the chunk's day, but
    started_at comes from node["start_date"] which FB omits on most collated results, so it was
    NULL for almost every row → the preload returned a couple of ids instead of thousands, the
    paginator counted every already-saved card as "new", and every one landed as
    skipped_already_reviewed (committed +0). Country-scoped preload is the correct, reliable set."""
    stmt = select(Ad.library_id).where(func.upper(Ad.country) == country.upper())
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return {r for r in rows if r}


async def _upsert_chunk_progress(
    session,
    config_id: int,
    date_from: date | None,
    date_to: date | None,
    cursor: str | None,
    has_next: bool,
    saved: int,
    media_type: str = "",
) -> None:
    """Bookmark pagination progress for this (config, date-chunk) in the SAME transaction
    as the batch's cards, so the cursor never runs ahead of what's saved."""
    df, dt = chunk_key(date_from, date_to)
    stmt = pg_insert(ChunkProgress).values(
        config_id=config_id, date_from=df, date_to=dt, media_type=media_type or "",
        last_cursor=cursor, collected_count=saved, has_next=has_next,
    ).on_conflict_do_update(
        index_elements=["config_id", "date_from", "date_to", "media_type"],
        set_={
            "last_cursor": cursor,
            "collected_count": ChunkProgress.collected_count + saved,
            "has_next": has_next,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def _scrape_single_period_graphql(
    url: str,
    config: ParsingConfig,
    uploader: MediaUploader,
    period_tag: str = "",
    cursor_start: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    track_chunk: bool = False,
    media_seg: str = "",
    max_ads: int | None = None,
    kw_override: str | None = None,
    only_day: date | None = None,
) -> dict:
    """GraphQL path: browser-less pagination (Phase 1) with legacy browser paths as fallback."""
    stats = dict(_EMPTY_STATS)

    # Сколько пачек подряд целиком промахнулись мимо целевого дня (см. _DAY_DONE_PACHEK).
    # Объявлено здесь, в _scrape_single_period_graphql: счётчик нужен именно вложенной
    # _persist_batch и должен жить столько же, сколько срез.
    _pustyh = {"n": 0}

    async def _persist_batch(nodes: list[dict], cursor: str | None, has_next: bool) -> int:
        """Map + upsert one batch and bookmark the cursor in a single transaction, so
        every committed batch survives a later crash and the cursor stays in lockstep with
        saved cards. One begin_nested savepoint per card isolates a bad row from the batch;
        upsert_ad already persists the direct-media URLs. Stats reflect what's in the DB.

        Returns a progress count (new+updated+errors) so the paginator's stall guard keys off
        real DB writes, not fetched-from-FB counts."""
        cards = [map_graphql_card(node) for node in nodes]
        # Сбор «за один день»: FB отдаёт по убыванию свежести и после целевого дня
        # продолжает выдавать более ранние. Чужие дни не сохраняем, а когда ВСЯ пачка
        # ушла за пределы дня — прекращаем листать: дальше будет только старше.
        if only_day is not None:
            _bylo = len(cards)
            cards = [c for c in cards
                     if c.started_at is None or c.started_at.date() == only_day]
            # Сколько карточек целевого дня ключ НАШЁЛ — включая те, что уже в базе.
            # Именно это число сопоставимо со счётчиком в интерфейсе библиотеки.
            stats["nayde_no"] = stats.get("nayde_no", 0) + len(cards)
            if _bylo and not cards:
                # Не бросаем срез на ПЕРВОЙ же пустой пачке: у крупных слов целевой день
                # может начинаться с десятой страницы и позже (замер по Hearing — с 11-й).
                _pustyh["n"] += 1
                if _pustyh["n"] >= _DAY_DONE_PACHEK:
                    raise _DayDone()
            elif cards:
                _pustyh["n"] = 0
        media_tasks: list[tuple[int, object]] = []
        saved = 0
        # Per-batch counters (the cumulative `stats` dict is updated from these after commit)
        # so the committed-batch log can show exactly where this batch's cards went.
        b = {"new": 0, "updated": 0, "skipped_already_reviewed": 0,
             "skipped_no_media": 0, "skipped_duplicate": 0, "errors": 0,
             "dup_kreativ": 0}
        batch_seen: set[str] = set()
        # Отпечатки креатива в ЭТОЙ пачке. Проверка по базе их не ловит: карточки
        # пачки ещё не записаны, поэтому десять одинаковых баннеров из одной выдачи
        # все проходили как новые — так за два часа просочилось 4 582 повтора.
        batch_fp: set[str] = set()

        # Слово и скелет для отсева — общий помощник на оба пути сбора.
        _slovo_dlya_otseva, _skelet_klyucha = _klyuch_dlya_otseva(config, kw_override)

        async with AsyncSessionLocal() as session:
            for card in cards:
                if not card.library_id or card.library_id in batch_seen:
                    if card.library_id:
                        b["skipped_duplicate"] += 1
                    continue
                batch_seen.add(card.library_id)
                if not card.image_urls and not card.video_urls and not card.poster_urls:
                    b["skipped_no_media"] += 1
                    continue
                # Сбор по ключу: слово должно реально встречаться в объявлении.
                # FB при нехватке совпадений добирает выдачу чем попало —
                # по «elastica» так пришло 9874 карточки, из них со словом 71.
                if _slovo_dlya_otseva and not _karta_po_klyuchu(
                        card, _slovo_dlya_otseva, _skelet_klyucha):
                    b["ne_po_klyuchu"] = b.get("ne_po_klyuchu", 0) + 1
                    continue
                # Отпечаток креатива = рекламодатель + текст + заголовок.
                # Имя медиафайла в отпечаток НЕ входит: FB выдаёт каждому объявлению
                # свой путь на CDN даже для одной и той же картинки, и повторы
                # проходили как уникальные (5.9% схлопывания вместо 57.8%).
                # Исключение — карточки вообще без текста: там отпечаток выродился бы
                # в «рекламодатель» и склеил бы весь его инвентарь, поэтому для них
                # различителем остаётся медиафайл.
                _telo = (card.body_text or "")[:400].strip()
                _zag = (card.title or "")[:200].strip()
                _media = ""
                for _spisok in (card.image_urls, card.video_urls):
                    if _spisok:
                        _media = (_spisok[0] or "").split("?")[0].rsplit("/", 1)[-1]
                        break
                # Файл обязателен: без него рекламодатель с сотнями разных
                # баннеров под одним текстом схлопывался в одну карточку.
                _razlichitel = _telo + _zag + _media
                _fp = _hashlib.md5(
                    ((getattr(card, "page_id", None) or "") + _razlichitel).encode("utf-8")
                ).hexdigest()
                if _fp in batch_fp:
                    b["dup_kreativ"] = b.get("dup_kreativ", 0) + 1
                    continue
                batch_fp.add(_fp)
                # Тот же баннер уже лежит в базе (другое объявление того же креатива).
                # Один индексный поиск по ix_ads_content_fp.
                # Тот же баннер уже в базе (другое объявление того же креатива).
                # Индекс ix_ads_fp_kolonka построен ПО КОЛОНКЕ (старый ix_ads_content_fp
                # был по сломанному выражению и потому не работал): Index Only Scan,
                # 0.2 мс. Через exists и без LIMIT — с LIMIT планировщик на отсутствующем
                # значении срывается в полный скан.
                try:
                    # Страна в условии обязательна: без неё креатив, собранный по
                    # одному гео, пропускался для всех остальных, и новое гео
                    # приходило почти пустым.
                    _est = await session.scalar(
                        _sa_text("select exists(select 1 from ads "
                                 "where content_fp = :fp and country = :strana)"),
                        {"fp": _fp, "strana": config.country})
                except Exception:
                    _est = False
                if _est:
                    b["dup_kreativ"] = b.get("dup_kreativ", 0) + 1
                    continue
                try:
                    async with session.begin_nested():  # savepoint: one bad card can't sink the batch
                        ad, is_new, skipped = await upsert_ad(
                            # Слово среза, а не config.keyword: у filters-конфигов
                            # он пуст, поэтому статистика «какое слово что нашло»
                            # не собиралась вовсе.
                            session, card, config.country,
                            kw_override or config.keyword,
                            config.vertical, config.config_type,
                        )
                except Exception as e:
                    logger.warning(f"[#{config.id}]{period_tag} DB error for {card.library_id}: {e}")
                    b["errors"] += 1
                    continue

                # ad=None — повтор креатива (тот же баннер у той же страницы),
                # он намеренно не сохраняется. Считаем отдельно, чтобы было видно
                # в логе пачки, сколько отсеяно повторов.
                if ad is None:
                    b["dup_kreativ"] = b.get("dup_kreativ", 0) + 1
                    continue

                if is_new:
                    b["new"] += 1
                    saved += 1
                    try:
                        ad.content_fp = _fp
                    except Exception:
                        pass
                elif skipped:
                    b["skipped_already_reviewed"] += 1
                else:
                    b["updated"] += 1
                    saved += 1

                if is_new and not skipped:
                    if card.image_urls or card.video_urls:
                        stats["urls_saved"] += 1
                    if settings.enable_media_download:
                        media_tasks.append((ad.id, card))

            await session.commit()  # cards land first — independent of the bookmark below

        for k, v in b.items():  # fold this batch into the cumulative chunk stats
            stats[k] = stats.get(k, 0) + v

        # Bookmark the cursor AFTER the cards are committed (so it can never point past
        # saved data) and in its OWN transaction, so a chunk_progress failure — e.g. the
        # table is missing because the deploy didn't run the migration — can't roll back
        # the batch of ads we just saved. Worst case the bookmark lags and the next run
        # re-collects a little (idempotent via upsert), which is the safe direction.
        if track_chunk:
            try:
                async with AsyncSessionLocal() as session:
                    await _upsert_chunk_progress(
                        session, config.id, date_from, date_to, cursor, has_next, saved, media_seg
                    )
                    await session.commit()
            except Exception as e:
                logger.warning(f"[#{config.id}]{period_tag} chunk_progress bookmark failed (cards saved): {e}")

        stats["raw"] += len(nodes)
        # Media dispatch is best-effort and runs AFTER the cards are committed — a transient
        # Redis blip enqueuing media jobs must not fail the chunk and force a full re-collect.
        try:
            phase2 = await _dispatch_media(media_tasks, config, uploader, period_tag)
        except Exception as e:
            logger.warning(f"[#{config.id}]{period_tag} media dispatch failed (cards saved): {e}")
            phase2 = {}
        for k, v in phase2.items():
            stats[k] = stats.get(k, 0) + v
        # One-line per-batch breakdown so "+0 committed" is explainable at a glance: shows
        # exactly where this batch's raw cards went (already-saved vs no-media vs error).
        logger.info(
            f"[#{config.id}]{period_tag} committed batch: +{saved} to DB "
            f"[new={b['new']} upd={b['updated']} skip_reviewed={b['skipped_already_reviewed']} "
            f"no_media={b['skipped_no_media']} dup={b['skipped_duplicate']} "
            f"ne_po_klyuchu={b.get('ne_po_klyuchu', 0)} "
            f"phash_dup={phase2.get('skipped_phash_duplicate', 0)} err={b['errors']}] "
            f"raw_batch={len(nodes)} has_next={has_next} "
            f"(chunk totals: new={stats['new']} upd={stats['updated']} raw={stats['raw']})"
        )
        # Progress signal for the stall guard. Counts as progress:
        #   saved (new+updated)      — real writes,
        #   errors                   — a broken batch isn't an exhausted one,
        #   skipped_no_media         — NEW ads were found (just dropped for lacking media);
        #                              a chunk still discovering ads must NOT be stall-closed,
        #                              otherwise a media-extraction regression looks like
        #                              "exhausted" and hides itself (see skipped_no_media log).
        # NOT counted: skipped_already_reviewed / already-in-DB dedup — that IS exhaustion.
        return saved + b["errors"] + b["skipped_no_media"]

    if settings.graphql_mode == "fetch":
        logger.info(
            f"[#{config.id}]{period_tag} starting browser-less pagination "
            f"(mode={settings.pagination_mode}, commit_batch={settings.commit_batch_size}, "
            f"resume_cursor={'yes' if cursor_start else 'no'})"
        )
        seen_ids: set[str] | None = None
        if track_chunk and settings.preload_seen_ids:
            # Preloading saved ids is a dedup OPTIMIZATION — a transient DB hiccup here must
            # not kill the chunk; worst case we re-process a few already-saved cards (idempotent).
            try:
                seen_ids = await _preload_seen_ids(config.country)
            except Exception as e:
                logger.warning(f"[#{config.id}]{period_tag} preload dedup ids failed (continuing): {e}")
                seen_ids = None
            if seen_ids:
                logger.info(f"[#{config.id}]{period_tag} preloaded {len(seen_ids)} saved ids into dedup")
        # Incremental + resumable: paginate() hands each commit_batch_size batch to
        # _persist_batch (cards + cursor bookmark), keeps only the current batch in memory.
        try:
            _schet_fb: dict = {}
            await paginate(url, start_cursor=cursor_start, on_batch=_persist_batch, seen_ids=seen_ids,
                           max_ads=max_ads, schet_naruzhu=_schet_fb)
            # Сколько карточек FB отдал всего, включая уже собранные ранее. Без этого
            # числа «получает много, качает мало» нечем было проверить: stats["raw"]
            # считает уже ПОСЛЕ отсечения знакомых номеров.
            stats["otdal_fb"] = stats.get("otdal_fb", 0) + _schet_fb.get("otdal_fb", 0)
        except _DayDone:
            logger.info(f"[#{config.id}]{period_tag} целевой день {only_day} исчерпан")
        except EmptyNoPagination:
            # FB не дал пагинацию: результатов мало (все на первой странице, курсора нет) ЛИБО 0.
            # Собираем через DOM-скролл — заберёт малые результаты (keyword+гео с 3-11 объявами),
            # на реально пустом вернёт 0. Так «нулевые» ключи с реальными объявами не теряются.
            logger.info(f"[#{config.id}]{period_tag} нет пагинации — DOM-fallback (малый/пустой результат)")
            dom_stats = await _scrape_single_period_playwright(url, config, uploader, period_tag)
            for k, v in dom_stats.items():
                stats[k] = stats.get(k, 0) + v
            # Закладку ставим ТОЛЬКО если что-то собрали. Пустой ответ фейсбука
            # означает и «объявлений нет», и «вы под ограничением» — различить
            # нельзя, а цена ошибки разная: помеченный ключ молчит до ручного
            # вмешательства. Пусто — не помечаем, следующий прогон попробует снова.
            if track_chunk and dom_stats.get("new", 0) > 0:
                try:
                    async with AsyncSessionLocal() as session:
                        await _upsert_chunk_progress(
                            session, config.id, date_from, date_to, None, False, dom_stats.get("new", 0), media_seg
                        )
                        await session.commit()
                except Exception as e:
                    logger.warning(f"[#{config.id}]{period_tag} chunk_progress bookmark (DOM-fallback) failed: {e}")
            elif track_chunk:
                logger.info(
                    f"[#{config.id}]{period_tag} пусто — закладку не ставлю "
                    f"(могло быть ограничение, а не конец выдачи)"
                )
        return stats

    # Legacy browser paths (non-default modes) still collect-then-upsert in one shot.
    if settings.graphql_mode == "page_fetch":
        logger.info(f"[#{config.id}]{period_tag} starting browser-GraphQL scrape (in-page fetch pagination)")
        raw_nodes = await scrape_via_page_fetch(url)
    else:
        logger.info(f"[#{config.id}]{period_tag} starting browser-GraphQL scrape (response interception)")
        raw_nodes = await scrape_via_browser_graphql(url)
    await _persist_batch(raw_nodes, None, False)
    return stats


async def _scrape_single_period_playwright(
    url: str,
    config: ParsingConfig,
    uploader: MediaUploader,
    period_tag: str = "",
) -> dict:
    """Playwright scroll path (fallback)."""
    stats = dict(_EMPTY_STATS)
    try:
        async with browser_context() as context:
            page = await context.new_page()
            loaded = await goto_with_challenge_retry(page, url)
            if not loaded:
                logger.warning(f"[#{config.id}]{period_tag} __rd_verify challenge persisted, skipping")
                return stats

            try:
                await page.wait_for_selector('div:has-text("Library ID")', timeout=20_000)
            except Exception:
                logger.warning(f"[#{config.id}]{period_tag} No 'Library ID' on page, probably empty results")
                return stats

            await asyncio.sleep(5)
            max_scrolls = 40 if config.config_type in ("filters", "fanpage") else 80
            raw_cards = await asyncio.wait_for(
                scroll_and_collect(page, max_scrolls=max_scrolls, stable_rounds=7),
                timeout=600,
            )
            stats["raw"] = len(raw_cards)

        cards = []
        for raw in raw_cards:
            card = parse_card_text(raw["text"])
            card.image_urls = [img["src"] for img in raw["images"]]
            card.video_urls = [v["src"] for v in raw["videos"] if v["src"]]
            card.poster_urls = [v["poster"] for v in raw["videos"] if v["poster"]]
            card.page_url = raw["page_url"]
            card.link_url = raw["external_url"]
            cards.append(card)

        phase1, media_tasks = await _upsert_cards_and_collect_media(cards, config, period_tag)
        for k, v in phase1.items():
            stats[k] = stats.get(k, 0) + v

        phase2 = await _dispatch_media(media_tasks, config, uploader, period_tag)
        for k, v in phase2.items():
            stats[k] = stats.get(k, 0) + v

    except Exception as exc:
        logger.error(f"[#{config.id}]{period_tag} Fatal Playwright error: {exc}")
        stats["errors"] += 1

    return stats


async def _scrape_single_period(
    url: str,
    config: ParsingConfig,
    uploader: MediaUploader,
    period_tag: str = "",
    cursor_start: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    track_chunk: bool = False,
    media_seg: str = "",
    max_ads: int | None = None,
    kw_override: str | None = None,
    only_day: date | None = None,
) -> dict:
    """Dispatch to GraphQL or Playwright path based on settings.use_graphql.

    When use_graphql is on, a GraphQL failure RAISES (the chunk job fails and RQ retries
    with a fresh IP) instead of silently falling back to the DOM-scroll path — that
    fallback is capped by max_scrolls + FB's DOM virtualisation and returns ~500 cards
    where GraphQL returns thousands, so reporting it as a "success" hides a degraded run.
    _scrape_single_period_playwright is kept intact and still used when use_graphql=False
    (explicit Playwright mode).
    """
    if settings.use_graphql:
        try:
            return await _scrape_single_period_graphql(
                url, config, uploader, period_tag, cursor_start, date_from, date_to, track_chunk, media_seg,
                max_ads=max_ads, kw_override=kw_override, only_day=only_day,
            )
        except Exception as exc:
            logger.error(
                f"[#{config.id}]{period_tag} GraphQL path failed: {exc} — failing chunk "
                f"(no silent DOM-scroll fallback); RQ re-enqueues with backoff + fresh IP "
                f"while retry attempts remain, then it's counted as an error"
            )
            raise RuntimeError(
                f"GraphQL scrape failed for config #{config.id}{period_tag}: {exc}"
            ) from exc
    return await _scrape_single_period_playwright(url, config, uploader, period_tag)


async def process_config(config: ParsingConfig, uploader: MediaUploader) -> dict:
    effective_date_from = config.date_from
    if config.auto_date_from_last_parse and config.last_parsed_at:
        effective_date_from = config.last_parsed_at.date()

    ad_type = (config.category or "all") if config.config_type in ("filters", "fanpage") else "all"

    # Date-range splitting: filters/fanpage configs with a multi-day period get split into
    # daily sub-queries to bypass FB's ~1700-card scroll cap per request.
    use_date_split = (
        config.config_type in ("filters", "fanpage")
        and effective_date_from is not None
        and config.date_to is not None
        and config.date_to > effective_date_from
    )

    total_stats = {
        "raw": 0, "new": 0, "updated": 0, "media_ok": 0, "media_fail": 0, "errors": 0,
        "skipped_duplicate": 0, "skipped_no_media": 0, "skipped_already_reviewed": 0,
        "skipped_phash_duplicate": 0, "removed_no_media": 0,
    }

    if use_date_split:
        # Single source of truth for chunk width: settings.chunk_days (same value the queue
        # coordinator slices on). Was hardcoded to 30 here, giving this legacy in-process path
        # a different granularity than the scaled path.
        chunks = split_date_range(effective_date_from, config.date_to, chunk_days=settings.chunk_days)
        logger.info(
            f"[#{config.id}] date-range split: {len(chunks)} sub-period(s) of "
            f"{settings.chunk_days}d ({effective_date_from} → {config.date_to})"
        )
        for i, (chunk_from, chunk_to) in enumerate(chunks, 1):
            period_tag = f" [month {i}/{len(chunks)} {chunk_from}]"
            url = build_library_url(
                config.country, config.keyword, config.languages,
                active_status=config.active_status or "all",
                media_type=config.media_type_filter or "all",
                platforms=config.platforms,
                date_from=chunk_from,
                date_to=chunk_to,
                advertiser=config.advertiser,
                ad_type=ad_type,
                sort_mode=getattr(config, "sort_mode", "total_impressions") or "total_impressions",
                sort_direction=getattr(config, "sort_direction", "desc") or "desc",
                is_targeted_country=getattr(config, "is_targeted_country", None),
            )
            logger.info(f"[#{config.id}] sub-period {i}/{len(chunks)}: {chunk_from} → {url}")
            chunk_stats = await _scrape_single_period(url, config, uploader, period_tag=period_tag)
            for k, v in chunk_stats.items():
                total_stats[k] = total_stats.get(k, 0) + v
            logger.info(
                f"[#{config.id}] sub-period {i}/{len(chunks)} done: "
                f"raw={chunk_stats['raw']} new={chunk_stats['new']} "
                f"total_so_far new={total_stats['new']}"
            )
            if i < len(chunks):
                pause = 3 if chunk_stats["raw"] == 0 else 12
                await asyncio.sleep(pause)
    else:
        url = build_library_url(
            config.country, config.keyword, config.languages,
            active_status=config.active_status or "all",
            media_type=config.media_type_filter or "all",
            platforms=config.platforms,
            date_from=effective_date_from,
            date_to=config.date_to,
            advertiser=config.advertiser,
            ad_type=ad_type,
            sort_mode=getattr(config, "sort_mode", "total_impressions") or "total_impressions",
            sort_direction=getattr(config, "sort_direction", "desc") or "desc",
            is_targeted_country=getattr(config, "is_targeted_country", None),
        )
        kw_tag = config.keyword or "(no keyword)"
        lang_tag = f" lang={config.languages}" if config.languages else ""
        type_tag = f" [{config.config_type}]"
        logger.info(f"[#{config.id}]{type_tag} {kw_tag}/{config.country}{lang_tag} → {url}")
        total_stats = await _scrape_single_period(url, config, uploader)

    # Stamp last_parsed_at so auto_date_from_last_parse advances on next run
    async with AsyncSessionLocal() as session:
        cfg = await session.get(ParsingConfig, config.id)
        if cfg:
            cfg.last_parsed_at = datetime.now(timezone.utc)
            await session.commit()

    logger.info(f"[#{config.id}] DONE: {total_stats}")
    return total_stats


def _build_url_for_config(config: ParsingConfig, date_from: date | None, date_to: date | None,
                          media_override: str | None = None,
                          platforms_override: list | None = None,
                          languages_override: list | None = None,
                          sort_direction_override: str | None = None,
                          ad_type_override: str | None = None,
                          active_status_override: str | None = None,
                          keyword_override: str | None = None,
                          emit_min: bool = False,
                          search_type: str = "keyword_unordered") -> str:
    """Build the Ad Library URL for one config over one date chunk (shared by run_once & chunk jobs).

    media_override — media-сегментация (image/video/meme); platforms_override — доп. сегментация
    по платформе (facebook/instagram/...). Оба перекрывают поля конфига, чтобы один filters-конфиг
    собирался многими параллельными срезами media×platform — так обходится потолок пагинации FB
    (каждый узкий срез < потолка, суммарно собираем больше).
    """
    ad_type = ad_type_override or (
        (config.category or "all") if config.config_type in ("filters", "fanpage") else "all")

    # Ключ в кавычках — это просьба искать точное совпадение, как в интерфейсе
    # библиотеки. Сами кавычки фейсбук ищет буквально и не находит ничего,
    # поэтому снимаем их и включаем фразовый режим.
    _klyuch = keyword_override or config.keyword
    if _klyuch:
        _ochishchen = _klyuch.strip()
        if len(_ochishchen) > 1 and _ochishchen[0] in '"\u00ab\u201c' and _ochishchen[-1] in '"\u00bb\u201d':
            _klyuch = _ochishchen[1:-1].strip()
            search_type = "keyword_exact_phrase"

    return build_library_url(
        config.country, _klyuch,
        languages_override or config.languages,
        active_status=active_status_override or config.active_status or "all",
        media_type=media_override or config.media_type_filter or "all",
        platforms=platforms_override or config.platforms,
        date_from=date_from,
        date_to=date_to,
        advertiser=config.advertiser,
        ad_type=ad_type,
        sort_mode=getattr(config, "sort_mode", "total_impressions") or "total_impressions",
        sort_direction=(sort_direction_override
                        or getattr(config, "sort_direction", "desc") or "desc"),
        is_targeted_country=getattr(config, "is_targeted_country", None),
        emit_min=emit_min,
        search_type=search_type,
    )


# Сколько часов закладка «чанк исчерпан» действительна. Должно совпадать с
# настройкой координатора, иначе один ставит задания, а другой их отбрасывает.
_ZAKLADKA_CHASOV = float(_os.getenv("ZAKLADKA_CHASOV", "20") or 20)


async def _skip_zombie_chunk(
    config_id: int, date_from: date | None, date_to: date | None, run_id: int | None,
    media_type: str | None = None,
) -> str | None:
    """Return a reason string if this chunk job should NOT run, else None.

    A worker restart kills the in-flight job → RQ marks it failed → Retry re-runs it later.
    Without this guard the re-run re-collects a day that's already closed, or keeps working for
    a run that was cancelled/finished. We check the live state right before doing any work:
      * chunk_progress.has_next=False → the day is exhausted, nothing to collect.
      * the owning ParserRun is cancelled/done/failed → stale job from an old/aborted run.
    """
    from app.models import ParserRun
    async with AsyncSessionLocal() as session:
        kdf, kdt = chunk_key(date_from, date_to)
        # media_type — часть ключа: у image/video/meme одного дня свои закладки
        prog = await session.get(ChunkProgress, (config_id, kdf, kdt, media_type or ""))
        if prog is not None and not prog.has_next:
            # Пропускаем, только если закладку поставил ЭТОТ ЖЕ прогон: защита
            # нужна от повтора задания внутри одного запуска (воркер упал →
            # RQ повторил), а не от нового запуска. Иначе координатор ставит
            # задания, а воркер их молча отбрасывает.
            if getattr(prog, "last_run_id", None) == run_id:
                return "chunk already exhausted (has_next=false)"
        if run_id is not None:
            run = await session.get(ParserRun, run_id)
            if run is None:
                return f"run #{run_id} no longer exists"
            if run.status in ("cancelled", "done", "failed"):
                return f"run #{run_id} is {run.status}"
    return None


async def process_chunk(
    config_id: int,
    date_from: date | None,
    date_to: date | None,
    cursor_start: str | None = None,
    run_id: int | None = None,
    media_type: str | None = None,
    platforms: list | None = None,
    languages: list | None = None,
    sort_direction: str | None = None,
    ad_type: str | None = None,
    active_status: str | None = None,
    keyword: str | None = None,
    emit_min: bool = False,
    max_ads: int | None = None,
    only_day: date | None = None,
    search_type: str = "keyword_unordered",
) -> dict:
    """Process ONE (config, date-chunk) unit — the RQ job body (Phase 2).

    Self-contained: own DB session, own MediaUploader. Idempotent via upsert_ad, so a
    crashed chunk can be safely re-run without restarting the whole geo.
    """
    # Zombie guard: a retried/orphaned job must not re-collect a closed day or work for a
    # cancelled/finished run (used to require hand-cleaning the RQ registries).
    skip_reason = await _skip_zombie_chunk(config_id, date_from, date_to, run_id, media_type)
    if skip_reason:
        period_tag = f" [{date_from}..{date_to}]" if date_from or date_to else ""
        logger.info(f"[#{config_id}]{period_tag} skipping chunk job — {skip_reason}")
        return {"skipped_zombie": 1}

    async with AsyncSessionLocal() as session:
        config = await session.get(ParsingConfig, config_id)
    if config is None:
        logger.error(f"[chunk] config #{config_id} not found")
        return {"error": f"config {config_id} not found"}

    uploader = MediaUploader()
    url = _build_url_for_config(config, date_from, date_to, media_override=media_type,
                                platforms_override=platforms, languages_override=languages,
                                sort_direction_override=sort_direction,
                                ad_type_override=ad_type,
                                active_status_override=active_status,
                                keyword_override=keyword, emit_min=emit_min,
                                search_type=search_type)
    _seg = "".join(f" [{x}]" for x in (media_type,
                                       (platforms[0] if platforms else None),
                                       (languages[0] if languages else None),
                                       (ad_type if ad_type else None),
                                       (active_status if active_status else None)) if x)
    period_tag = (f" [{date_from}..{date_to}]" if date_from or date_to else "") + _seg
    logger.info(f"[#{config_id}]{period_tag} chunk start → {url}")

    # Media-сегменты теперь ТОЖЕ трекаются: media_type входит в ключ chunk_progress,
    # коллизии PK между image/video/meme больше нет. Без этого тяжёлый срез после падения
    # начинал с первой страницы и на 17-32 тыс. карточек не доходил до конца.
    # Точная фраза — ОТДЕЛЬНЫЙ набор объявлений, и закладка ей нужна своя.
    # Раньше оба варианта одного ключа писали в (config, даты, "") и мешали друг
    # другу: фразовый срез мог продолжить с курсора разбросного (курсор чужой
    # выдачи), а «исчерпан» от одного гасило другой. Пока фраза включалась только
    # для многословных ключей, это било по 23 конфигам; теперь она у всех 143.
    _seg_key = (media_type or "") + ("|fraza" if search_type == "keyword_exact_phrase" else "")
    # У языковых и платформенных срезов свой набор объявлений, а ключ chunk_progress
    # состоит только из (config, даты, media). Резюм и проверку исчерпания для них
    # пропускаем: иначе языковую джобу убивает «срез уже исчерпан», выставленное
    # медиа-срезом с теми же датами.
    if (languages is not None or sort_direction is not None or ad_type is not None
            or active_status is not None or keyword is not None):
        cursor_start = cursor_start
    elif cursor_start is None:
        kdf, kdt = chunk_key(date_from, date_to)
        async with AsyncSessionLocal() as session:
            prog = await session.get(ChunkProgress, (config_id, kdf, kdt, _seg_key))
        if prog is not None and not prog.has_next:
            logger.info(f"[#{config_id}]{period_tag} срез уже исчерпан (has_next=false) — пропускаю")
            return {"skipped_done": 1}
        if prog is not None and prog.last_cursor:
            cursor_start = prog.last_cursor
            logger.info(
                f"[#{config_id}]{period_tag} возобновляю с сохранённого курсора "
                f"(собрано ранее {prog.collected_count})"
            )

    _nachalo_sreza = time.time()
    stats = await _scrape_single_period(
        url, config, uploader, period_tag, cursor_start,
        date_from=date_from, date_to=date_to,
        track_chunk=(platforms is None and languages is None and sort_direction is None
                     and ad_type is None and active_status is None
                     and keyword is None and not emit_min),
        media_seg=_seg_key,
        max_ads=max_ads,
        kw_override=keyword,
        only_day=only_day,
    )

    # Событие о закрытом срезе — для живого лога в админке. Пишем отдельной короткой
    # записью: очередь RQ из API не видна (нет модуля redis), а знать, что именно
    # сейчас перебирается и с каким выхлопом, нужно.
    try:
        async with AsyncSessionLocal() as _s:
            await _s.execute(text(
                "INSERT INTO slice_events (config_id, country, den, slovo, media,"
                " status, novyh, vsego, sekund, nayde_no, run_id) VALUES (:c, :co, :d, :w, :m,"
                " :st, :n, :v, :sec, :nd, :rid)"),
                {"c": config_id, "co": getattr(config, "country", None),
                 "d": only_day, "w": keyword, "m": media_type,
                 "st": active_status, "n": int(stats.get("new", 0)),
                 "v": int(stats.get("raw", 0)),
                 "sec": int(time.time() - _nachalo_sreza),
                 "nd": int(stats.get("nayde_no", 0)),
                 # Номер прогона: без него сводка привязывает срез по времени и
                 # приписывает чужому прогону тот, что закрылся после его старта.
                 "rid": run_id})
            await _s.commit()
    except Exception as _e:
        logger.warning(f"[#{config_id}] не записал событие среза: {type(_e).__name__}")

    async with AsyncSessionLocal() as session:
        cfg = await session.get(ParsingConfig, config_id)
        if cfg:
            cfg.last_parsed_at = datetime.now(timezone.utc)
            await session.commit()

    logger.info(f"[#{config_id}]{period_tag} chunk done: {stats}")
    return stats


CONFIG_CONCURRENCY = 3


async def run_once(
    limit: int | None = None,
    config_id: int | None = None,
    parallel: bool = True,
) -> None:
    ip = await current_ip()
    logger.info(f"Starting worker. Current IP: {ip}")

    async with AsyncSessionLocal() as session:
        configs = await get_active_configs(session, config_id=config_id)

    if not configs and config_id is not None:
        logger.error(f"Config #{config_id} not found")
        return

    if limit:
        configs = configs[:limit]

    mode = f"parallel (concurrency={CONFIG_CONCURRENCY})" if parallel else "sequential"
    logger.info(f"Loaded {len(configs)} config(s) — mode: {mode}{f' (single: #{config_id})' if config_id else ''}")

    uploader = MediaUploader()
    total = {
        "raw": 0, "new": 0, "updated": 0, "media_ok": 0, "media_fail": 0, "errors": 0,
        "skipped_duplicate": 0, "skipped_no_media": 0, "skipped_already_reviewed": 0,
        "skipped_phash_duplicate": 0, "removed_no_media": 0,
    }

    if parallel and not config_id:
        sem = asyncio.Semaphore(CONFIG_CONCURRENCY)

        async def _run_with_sem(config: ParsingConfig) -> dict:
            async with sem:
                logger.info(f"[parallel] starting config #{config.id} ({config.keyword or 'no-kw'}/{config.country})")
                result = await process_config(config, uploader)
                logger.info(f"[parallel] finished config #{config.id}: new={result.get('new', 0)}")
                return result

        results = await asyncio.gather(
            *[_run_with_sem(cfg) for cfg in configs],
            return_exceptions=True,
        )
        for cfg, r in zip(configs, results):
            if isinstance(r, Exception):
                logger.error(f"[parallel] config #{cfg.id} raised: {r}")
                total["errors"] = total.get("errors", 0) + 1
            else:
                for k, v in r.items():
                    total[k] = total.get(k, 0) + v
    else:
        for i, config in enumerate(configs, 1):
            logger.info(f"--- [{i}/{len(configs)}] config #{config.id} ---")
            stats = await process_config(config, uploader)
            for k, v in stats.items():
                total[k] = total.get(k, 0) + v

            if i < len(configs):
                logger.info("Rotating IP before next config")
                await rotate_ip()

    logger.info(f"=== WORKER FINISHED. Totals: {total} ===")
    return total


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run parser worker")
    parser.add_argument("limit", nargs="?", type=int, default=None,
                        help="Max number of active configs to process (positional)")
    parser.add_argument("--config-id", type=int, default=None,
                        help="Run a single config by ID (ignores is_active)")
    args = parser.parse_args()
    asyncio.run(run_once(limit=args.limit, config_id=args.config_id))
