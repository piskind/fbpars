"""Discovery coordinator (Phase 2).

Fans a discovery run out into RQ chunk jobs, then monitors them to completion and
aggregates their stats — preserving the existing ParserRun trigger lifecycle
(triggered → running → done) that the admin API and scheduler rely on.

One discovery run is monitored at a time (same as the old in-process run_once), but the
actual pagination work now runs in parallel across the parser-worker replicas.
"""
import asyncio
import os
from datetime import date, datetime, timedelta, timezone

from loguru import logger

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import ParserRun, ChunkProgress, ParsingConfig, chunk_key
from app.worker import get_active_configs, split_date_range

_STAT_KEYS = (
    "raw", "new", "updated", "media_ok", "media_fail", "errors",
    "skipped_duplicate", "skipped_no_media", "skipped_already_reviewed",
    "skipped_phash_duplicate", "removed_no_media",
)

# RQ terminal statuses (canceled/stopped spellings vary across rq versions).
_TERMINAL = {"finished", "failed", "canceled", "cancelled", "stopped"}

# Set by app.main's SIGTERM/SIGINT handler so monitor_run can bail out promptly (persisting
# stats) instead of leaving the coordinator to be SIGKILLed 10s into a docker restart.
shutdown_event = asyncio.Event()


def _iso_to_date(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


_GEO_BY_CONFIG: dict = {}  # config_id -> country, заполняется в enqueue_run для админ-логов

# Media-срезы для параллельного сбора filters-конфигов. Непересекающиеся по типу медиа
# (пересечения дедуплятся upsert'ом по library_id). Покрывают все объявы с медиа; 'none'
# (текст без медиа) не берём — worker всё равно их пропускает (skipped_no_media).
_MEDIA_SEGMENTS = ("image", "video", "meme")

# Сетка «максимального сбора» (config.max_collect=true). FB обрезает КАЖДЫЙ набор
# параметров, поэтому объём даёт не глубина одного среза, а число непересекающихся.
# Замерено 09.08.2026 на US: язык — жёсткий серверный фильтр (в отличие от гео),
# узкий языковой срез отдаёт свой набор целиком; active/inactive пересекаются на 0%.
_MAX_STATUS = ("active", "inactive")
_MAX_LANGS = ("en", "es", "pt", "fr", "de", "it", "ru", "zh", "ja", "ko", "vi", "tl",
              "ar", "hi", "th", "tr", "id", "pl", "nl", "sv", "he", "fa", "el", "uk")


# Сколько часов закладка «чанк исчерпан» считается действительной. Дольше —
# собираем заново: библиотека пополняется каждый день.
_ZAKLADKA_CHASOV = float(os.getenv("ZAKLADKA_CHASOV", "20") or 20)


_DAY_SWEEP_MODE = "relevancy_monthly_grouped"
_DAY_SWEEP_CAP = int(os.getenv("DAY_SWEEP_CAP", "3000") or 3000)
# Берём ВЕСЬ проверенный список. Ограничение в 2000 стояло ради срока: замер
# показал, что топ-2000 дают 87% объёма, а остаток удваивает время прогона.
# Раз объём важнее — снято.
_DAY_SWEEP_WORDS_TOP = 100000


# Гонять только слова из proven_words.json, игнорируя историю и остальной словарь.
_TOLKO_SPISOK = (os.getenv("SPISOK_SLOV_TOLKO", "false") or "false").lower() in ("1", "true", "yes")


def _proven_words() -> list[str]:
    """Слова, доказавшие отдачу на прогоне 14-15.08 (US, 1 июня), по убыванию.

    Перебирать словарь вслепую незачем: из 40 000 слов отдачу дали 3 895, причём
    топ-2000 из них дают 87% объёма. Файл готовится скриптом из результатов прогона.
    """
    import json
    try:
        # Полный словарь: 40 000 слов. Проверенный список из 4 000 выведен на
        # ВЫДАЧЕ США — для другого гео рабочими будут другие слова, и фильтрация
        # по US-проверенным резала охват. Сравнение: США с 40k слов дали 310 311,
        # Канада с 4k — 58 381.
        slova = []
        try:
            with open("/app/proven_words.json", encoding="utf-8") as f:
                slova = json.load(f)          # проверенные — первыми
        except Exception:
            pass
        if _TOLKO_SPISOK:
            # Гоним ровно почищенный список, большой словарь не подмешиваем.
            return slova
        est = set(slova)
        with open("/app/kw_dict_big.txt", encoding="utf-8") as f:
            for line in f:
                w = line.strip()
                if w and w not in est:
                    slova.append(w)
        return slova[:_DAY_SWEEP_WORDS_TOP]
    except Exception as exc:
        logger.warning(f"[coordinator] список проверенных слов недоступен: {exc}")
        return []


# ── Умный план дневного сбора (SMART_SWEEP) ──────────────────────────────────
# Перебирать весь словарь по каждому гео — это 55% времени парка ради 14% крео
# (замер по CA, см. заголовок patch_smart_sweep.py). Вместо этого: сначала оси с
# высокой отдачей на срез, слова — только те, что дали отдачу НА ЭТОМ ГЕО.
_SMART_SWEEP = (os.getenv("SMART_SWEEP", "true") or "true").lower() not in ("0", "false", "no")
# Порог: слово попадает в план, если на этом гео когда-либо дало столько крео.
_SWEEP_WORD_MIN = int(os.getenv("SWEEP_WORD_MIN", "5") or 5)
# «Жирное» слово — гоняем глубоко: потолок 400 резал их сильнее всего.
_SWEEP_WORD_DEEP_MIN = int(os.getenv("SWEEP_WORD_DEEP_MIN", "20") or 20)
_SWEEP_DEEP_CAP = int(os.getenv("SWEEP_DEEP_CAP", "20000") or 20000)
# Разведка: сколько НЕпройденных слов подмешать, чтобы список гео не окостенел.
_SWEEP_RAZVEDKA = int(os.getenv("SWEEP_RAZVEDKA", "1500") or 1500)
# Возраст целевого дня, до которого считаем его «свежим» и берём оба статуса.
_SWEEP_SVEZHIY_DNEY = int(os.getenv("SWEEP_SVEZHIY_DNEY", "45") or 45)
# Ниже этого числа своя история считается тонкой и добирается общей по всем гео.
_SWEEP_MIN_ISTORIYA = int(os.getenv("SWEEP_MIN_ISTORIYA", "1000") or 1000)
_SWEEP_OBSHCHIH_MAX = int(os.getenv("SWEEP_OBSHCHIH_MAX", "4500") or 4500)
# Языки для языковой сетки, если у конфига не задан свой пул.
_SWEEP_LANGS = ("zh", "ar", "pt", "th", "tr", "el", "it", "de", "he", "nl", "id", "fr",
                "sv", "es", "vi", "en", "ja", "ko", "ru", "hi", "pl", "uk", "fa", "tl")


async def _slova_obshchie(session, skolko: int) -> dict:
    """Слова, давшие отдачу на ЛЮБОМ гео — прайор для гео с тонкой историей."""
    from sqlalchemy import text
    try:
        rows = (await session.execute(text("""
            select slovo, sum(novyh) as novyh
            from slice_events
            where slovo is not null
            group by slovo having sum(novyh) >= :m
            order by 2 desc limit :n
        """), {"m": _SWEEP_WORD_MIN, "n": skolko})).all()
    except Exception:
        return {}
    return {r[0]: int(r[1]) for r in rows}


async def _slova_geo(session, strana: str) -> dict:
    """Слова, давшие отдачу НА ЭТОМ ГЕО: {слово: сколько крео принесло}.

    Источник — журнал срезов, а не словарь: рабочий набор у каждого гео свой (список,
    выведенный на выдаче США, резал охват Канады вдвое — см. конспект 19.08).
    """
    from sqlalchemy import text
    try:
        rows = (await session.execute(text("""
            select slovo, sum(novyh) as novyh
            from slice_events
            where slovo is not null and country = :c
            group by slovo having sum(novyh) >= :m
            order by 2 desc
        """), {"c": strana, "m": _SWEEP_WORD_MIN})).all()
    except Exception as exc:
        logger.warning(f"[coordinator] не смог прочитать историю слов по {strana}: {exc}")
        return {}
    svoi = {r[0]: int(r[1]) for r in rows}
    if len(svoi) >= _SWEEP_MIN_ISTORIYA:
        return svoi
    # История тонкая (гео собирали до появления журнала срезов) — добираем общей.
    obshchie = await _slova_obshchie(session, _SWEEP_OBSHCHIH_MAX)
    for slovo, novyh in obshchie.items():
        svoi.setdefault(slovo, novyh)
    logger.info(
        f"[coordinator] {strana}: своих слов с отдачей мало — список добран общими "
        f"до {len(svoi)}"
    )
    return svoi


async def _rodnye_slova(session, yazyki: list) -> dict:
    """{язык: [слова]} — родные слова языка из таблицы keywords.

    Латиницу (istochnik='dict40k') сюда не берём: она уже перебирается общим словарём,
    а здесь нас интересуют именно корпуса, до которых латинским словом не достать.
    """
    from sqlalchemy import text
    if not yazyki:
        return {}
    try:
        rows = (await session.execute(text("""
            select yazyk, slovo from keywords
            where aktivno and istochnik = 'native' and yazyk = any(:ya)
            order by yazyk, kategoriya, slovo
        """), {"ya": list(yazyki)})).all()
    except Exception as exc:
        logger.warning(f"[coordinator] таблица keywords недоступна ({exc}) — родные слова пропускаю")
        return {}
    out: dict = {}
    for yazyk, slovo in rows:
        out.setdefault(yazyk, []).append(slovo)
    return out


def _yazyki_geo(config) -> list:
    """Пул языков гео: из конфига, иначе общий список."""
    svoi = getattr(config, "languages", None)
    if svoi:
        return list(svoi)
    return list(_SWEEP_LANGS)


def _plan_dnya(config, slova_geo: dict, slovar: list, rodnye: dict | None = None,
               den=None) -> list[dict]:
    """Срезы на один день по убыванию отдачи. Возвращает список kwargs (без дат и run_id)."""
    plan: list[dict] = []

    # Режим «только заданный список»: историю гео и разведку урезаем до слов из
    # proven_words.json. Иначе перебор пошёл бы по тысячам слов из истории, и
    # смысл почищенного списка терялся.
    if _TOLKO_SPISOK and slovar:
        _razresheno = {w.strip().lower() for w in slovar if w and w.strip()}
        slova_geo = {w: n for w, n in (slova_geo or {}).items()
                     if w.strip().lower() in _razresheno}
        _nedostayushchie = [w for w in slovar if w.strip().lower() not in
                            {k.strip().lower() for k in slova_geo}]
        # Слова из списка, которых на этом гео ещё не пробовали, идут разведкой.
        slovar = _nedostayushchie

    # Статусы для словесных срезов зависят от возраста целевого дня: на свежем дне
    # реклама ещё крутится и лежит в active, на старом — уже остановлена и лежит в
    # inactive (замер: на дне 15-дневной давности inactive-срезы отдавали ноль карточек).
    _vozrast = (date.today() - den).days if den else 10 ** 6
    _statusy = _MAX_STATUS if _vozrast < _SWEEP_SVEZHIY_DNEY else ("inactive",)

    # 1. Базовая сетка: дёшево и покрывает верхушку выдачи.
    for st in _MAX_STATUS:
        for mt in _MEDIA_SEGMENTS:
            plan.append({"tag": f"{st}_{mt}", "active_status": st, "media_type": mt,
                         "max_ads": _DAY_SWEEP_CAP})

    # 2. Языковая сетка. Замер: 14.4 крео на срез — лучшая ось. Только inactive:
    # active-срезы давали 0-15 против 30-89 и закрывались с пустой выдачей.
    for lang in _yazyki_geo(config):
        for mt in _MEDIA_SEGMENTS:
            for st in _MAX_STATUS:
                plan.append({"tag": f"lang-{lang}_{mt}_{st}", "active_status": st,
                             "media_type": mt, "languages": [lang],
                             "max_ads": _SWEEP_DEEP_CAP})

    # 2b. Родные слова языка × медиа. Латинский словарь до корпусов zh/ar/th/ja/ko/he
    # не достаёт вовсе, а перебор словом там работает: ar «бесплатно» дал 54 новых из
    # 100 карточек — столько же, сколько весь языковой срез.
    _polnyy = bool(getattr(config, "max_collect", False))

    for yazyk, slova in ((rodnye or {}) if _polnyy else {}).items():
        for slovo in slova:
            bezopasno = "".join(c if c.isalnum() else "-" for c in slovo.lower())[:40]
            for mt in _MEDIA_SEGMENTS:
                for st in _statusy:
                    plan.append({"tag": f"nat-{yazyk}-{bezopasno}_{mt}_{st}", "active_status": st,
                                 "media_type": mt, "languages": [yazyk], "keyword": slovo,
                                 "max_ads": _SWEEP_DEEP_CAP})

    if not _polnyy:
        # «Проба»: базовая + языковая сетка и всё. Перебор по словам — это часы и
        # десятки тысяч срезов, он относится к полной глубине.
        return plan

    if slova_geo:
        # 3. Слова, доказавшие отдачу на этом гео, каждое × медиа (замер: ×4 против
        # среза без медиа-фильтра). Жирным — глубокий потолок.
        for slovo, novyh in slova_geo.items():
            cap = _SWEEP_DEEP_CAP if novyh >= _SWEEP_WORD_DEEP_MIN else _DAY_SWEEP_CAP
            bezopasno = "".join(c if c.isalnum() else "-" for c in slovo.lower())
            for mt in _MEDIA_SEGMENTS:
                for st in _statusy:
                    plan.append({"tag": f"kw-{bezopasno}_{mt}_{st}", "active_status": st,
                                 "media_type": mt, "keyword": slovo, "max_ads": cap})
        # 4. Разведка: слова, которых на этом гео ещё не пробовали.
        neprov = [w for w in slovar if w not in slova_geo][:_SWEEP_RAZVEDKA]
        for slovo in neprov:
            bezopasno = "".join(c if c.isalnum() else "-" for c in slovo.lower())
            for st in _statusy:
                plan.append({"tag": f"razv-{bezopasno}_{st}", "active_status": st,
                             "keyword": slovo, "max_ads": _DAY_SWEEP_CAP})
    else:
        # Гео без истории: первый проход = разведка всем словарём, из неё и родится
        # список результативных слов для следующих дней.
        for slovo in slovar:
            bezopasno = "".join(c if c.isalnum() else "-" for c in slovo.lower())
            # Только inactive: там основная масса дня, а ещё крутящуюся рекламу
            # закрывают базовая и языковая сетки. Вдвое дешевле перебора по двум статусам.
            for st in _statusy:
                plan.append({"tag": f"kw-{bezopasno}_{st}", "active_status": st,
                             "keyword": slovo, "max_ads": _DAY_SWEEP_CAP})
    # Многословные ключи дублируем точной фразой. keyword_unordered ищет слова
    # порознь («nolimit city» ловит рекламу, где есть city, но нет nolimit) —
    # это даёт объём, но заказчик по такому ключу видит мусор. Фразовый срез
    # добирает то же слово как единое целое.
    frazy = []
    for srez in plan:
        slovo = srez.get("keyword")
        if slovo and len(slovo.split()) > 1:
            kopiya = dict(srez)
            kopiya["tag"] = "fraza-" + srez["tag"]
            kopiya["search_type"] = "keyword_exact_phrase"
            frazy.append(kopiya)
    plan.extend(frazy)
    return plan


def _chunk_label(config_id, df_iso: str | None, dt_iso: str | None) -> str:
    """Readable '#<cfg> <geo> [df..dt]' for the admin log, from a job's kwargs."""
    geo = _GEO_BY_CONFIG.get(config_id, "")
    tag = (f"#{config_id} {geo}").rstrip()
    if df_iso or dt_iso:
        return f"{tag} [{df_iso}..{dt_iso}]"
    return tag


async def _persist_run_stats(run_id: int, total: dict) -> None:
    """Write the running aggregate to parser_runs.stats NOW, so a cancel / crash / SIGKILL
    keeps whatever finished chunks contributed (Task: stats were only written once at the
    very end, and the coordinator is frequently killed before it gets there)."""
    try:
        async with AsyncSessionLocal() as session:
            run = await session.get(ParserRun, run_id)
            if run:
                run.stats = dict(total)
                await session.commit()
    except Exception as exc:
        logger.warning(f"[coordinator] run #{run_id}: could not persist stats: {exc}")


async def _persist_chunk_result(run_id: int, config_id, df_iso: str | None, dt_iso: str | None, result: dict) -> None:
    """Store a finished chunk's final stats in chunk_progress (last_stats/last_run_id) so the
    per-chunk 'done' totals survive a docker image rebuild — logs get wiped, the DB doesn't.
    UPDATE-only: a chunk that saved nothing has no row and needs none (it collected nothing)."""
    if config_id is None:
        return
    try:
        kdf, kdt = chunk_key(_iso_to_date(df_iso), _iso_to_date(dt_iso))
        async with AsyncSessionLocal() as session:
            row = await session.get(ChunkProgress, (config_id, kdf, kdt, ""))
            if row is not None:
                row.last_stats = {k: v for k, v in result.items() if isinstance(v, int)}
                row.last_run_id = run_id
                await session.commit()
    except Exception as exc:
        logger.warning(f"[coordinator] could not persist chunk result for #{config_id}: {exc}")


# Сколько карточек должен был взять срез, чтобы «не исчерпался» значило «бездонный»,
# а не «оборвался в самом начале». Ниже порога это транспортный сбой, а не объём.
_POROG_BEZDONNOSTI = int(os.getenv("POROG_BEZDONNOSTI", "2000") or 2000)


async def _pometit_bezdonnym(config_id, df_iso: str | None, dt_iso: str | None) -> None:
    """Срез закончился, а выдача — нет: помечаем ключ для сбора сеткой.

    Один последовательный срез бездонный ключ не берёт в принципе: FB отдаёт по 10
    карточек за запрос и режет каждый набор параметров. Такой конфиг должен в следующий
    прогон уйти на сетку язык x статус x медиа — она расходится по 48 воркерам.
    Решение принимает система, руками флаг больше держать не нужно; в админке он виден
    и его можно снять.
    """
    if config_id is None:
        return
    try:
        kdf, kdt = chunk_key(_iso_to_date(df_iso), _iso_to_date(dt_iso))
        async with AsyncSessionLocal() as session:
            row = await session.get(ChunkProgress, (config_id, kdf, kdt, ""))
            if row is None:
                return
            # Критерий — ОБЪЁМ, а не has_next. Замер 02.09: из 136 закладок прогона 318
            # has_next=true осталось ровно у одной, при этом Elastica/PE закрылась
            # «исчерпанной» на 22 476 карточках. has_next=false означает «кончился ЭТОТ
            # набор параметров», а не «собрано всё»: у DiaStop/UZ несегментированный срез
            # тоже закрывался, а восемь узких добавили сверху x4.3. Поэтому сеткой берём
            # то, что даёт большой объём одним срезом — там наверняка есть и остальное.
            if (row.collected_count or 0) < _POROG_BEZDONNOSTI:
                return
            cfg = await session.get(ParsingConfig, config_id)
            if cfg is None or getattr(cfg, "config_type", "") != "keyword":
                return
            if getattr(cfg, "max_collect", False):
                return
            cfg.max_collect = True
            await session.commit()
            logger.info(
                f"[coordinator] #{config_id} {cfg.keyword}/{cfg.country}: срез дал "
                f"{row.collected_count} карточек одним набором параметров — ключ признан "
                f"объёмным, следующий прогон соберёт его сеткой "
                f"({len(_MAX_LANGS)}x{len(_MAX_STATUS)}x{len(_MEDIA_SEGMENTS)} срезов)"
            )
    except Exception as exc:
        logger.warning(f"[coordinator] не смог пометить #{config_id} бездонным: {exc}")


async def _log_chunk_progress(coords: list, progress_seen: dict, started_seen: set) -> None:
    """Surface live collection into the admin log_tail by reading chunk_progress (the workers
    run in separate processes, so their own stdout never reaches the run's log sink). Logs a
    'collecting' line the first time a chunk appears and a '+N cards' delta as it grows."""
    if not coords:
        return
    try:
        async with AsyncSessionLocal() as session:
            for config_id, kdf, kdt, label in coords:
                row = await session.get(ChunkProgress, (config_id, kdf, kdt, ""))
                if row is None:
                    continue
                key = (config_id, kdf, kdt)
                prev = progress_seen.get(key)
                if key not in started_seen:
                    started_seen.add(key)
                    logger.info(
                        f"[coordinator] chunk {label} collecting: "
                        f"{row.collected_count} saved so far (has_next={row.has_next})"
                    )
                elif prev is not None and row.collected_count != prev:
                    logger.info(
                        f"[coordinator] chunk {label}: committed batch +{row.collected_count - prev} "
                        f"cards (total {row.collected_count}, has_next={row.has_next})"
                    )
                progress_seen[key] = row.collected_count
    except Exception as exc:
        logger.warning(f"[coordinator] chunk-progress log failed: {exc}")


def _okno_do_segodnya(config, date_to: date | None) -> date | None:
    """Граница сбора: не дальше замороженной даты, а до сегодня.

    У filters-конфигов date_to замерзает на дате последней ручной правки. На
    02.09.2026 все шестнадцать активных собирали до прошлого — от 28 февраля до
    24 июля, и всё свежее по этим гео в план не попадало вовсе. Конфиг НЕ
    переписываем: в админке остаётся то, что задал человек, расширяется только план,
    и это видно в логе.

    Посуточный режим не трогаем — там date_to это целевой день, а не граница.
    """
    if (getattr(config, "config_type", "") not in ("filters", "fanpage")
            or (getattr(config, "sort_mode", "") or "") == _DAY_SWEEP_MODE
            or date_to is None):
        return date_to
    segodnya = datetime.now(timezone.utc).date()
    if date_to >= segodnya:
        return date_to
    logger.info(
        f"[coordinator] #{getattr(config, 'id', '?')} {getattr(config, 'country', '?')}: "
        f"окно расширено {date_to} → {segodnya} (в конфиге дата заморожена)"
    )
    return segodnya


def _chunks_for(config) -> list[tuple[date | None, date | None]]:
    """Mirror worker.process_config's date-splitting decision, but down to chunk_days."""
    effective_date_from = min(config.date_from or date(2019, 1, 1), date(2019, 1, 1))
    if config.auto_date_from_last_parse and config.last_parsed_at:
        effective_date_from = config.last_parsed_at.date()

    # Окно сбора должно доходить до сегодня. У filters-конфигов date_to замерзает
    # на дате, когда его последний раз правили руками: на 02.09.2026 все шестнадцать
    # активных собирали до прошлого — от 28 февраля до 24 июля, и всё свежее по этим
    # гео просто не попадало в план. Конфиг НЕ переписываем: в админке остаётся то,
    # что задал человек, расширяется только план, и это видно в логе.
    # Посуточный режим не трогаем — там date_to это целевой день, а не граница.
    effective_date_to = _okno_do_segodnya(config, config.date_to)

    use_split = (
        config.config_type in ("filters", "fanpage")
        and effective_date_from is not None
        and effective_date_to is not None
        and effective_date_to > effective_date_from
    )
    if use_split:
        return split_date_range(effective_date_from, effective_date_to, chunk_days=settings.chunk_days)
    return [(effective_date_from, effective_date_to)]


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


async def _pochistit_ochered(q, conn, run_id: int) -> int:
    """Убрать из очереди задания прогонов, которые уже не активны.

    Координатор снимает свои задания при отмене, но только если жив. Если его
    перезапустили или он упал, тысячи заданий остаются в очереди, и следующий
    прогон встаёт за ними: 02.09 прогон #325 с 45 срезами не начался, потому что
    перед ним висело 3 982 задания отменённого #324. Воркеры такие задания
    пропускают как зомби, но сам разбор очереди занимает часы.

    Чистим на старте прогона — так система лечится сама, без ручного вмешательства.
    """
    from rq.job import Job
    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(_text_ochered(
                "SELECT id FROM parser_runs WHERE status IN ('triggered','running')"))).all()
        zhivye = {r[0] for r in rows}
        zhivye.add(run_id)
        ubrano = 0
        for jid in q.get_job_ids():
            try:
                job = Job.fetch(jid, connection=conn)
            except Exception:
                continue
            rid = (job.kwargs or {}).get("run_id")
            if rid is not None and rid not in zhivye:
                try:
                    job.cancel()
                    ubrano += 1
                except Exception:
                    pass
        if ubrano:
            logger.info(
                f"[coordinator] очередь почищена: убрано {ubrano} заданий мёртвых прогонов "
                f"(иначе новый прогон ждёт их разбора)"
            )
        return ubrano
    except Exception as exc:
        logger.warning(f"[coordinator] чистка очереди не удалась: {exc}")
        return 0


def _text_ochered(sql: str):
    from sqlalchemy import text
    return text(sql)


# Сколько объявлений должен уже дать ключ, чтобы собирать его сеткой.
# 300 подобрано по данным 03.09: порог даёт 22 конфига и ~3 400 срезов на прогон
# (полтора-два часа). Ниже — в сетку лезут ключи без инвентаря и жгут срезы впустую,
# выше — под неё не попадают работающие бренды вроде Artronol/IT (355 объявлений).
_POROG_SETKI = int(os.getenv("POROG_SETKI", "300") or 300)


async def _podklyuchit_setku() -> int:
    """Ключ, который уже дал объём, переводим на сбор сеткой — сам, без человека.

    Признак простой и устойчивый: сколько объявлений по этому ключу и гео уже лежит
    в базе. Замер 03.09 показал, что сетка берёт то, до чего обычный срез не доходит:
    у ArtiZynt/ES шесть срезов из ста сорока четырёх дали 16 объявлений сверх
    собранного (язык en — 11, статус inactive — 9), хотя ключ считался вычерпанным.

    Только ПОДКЛЮЧАЕМ, никогда не снимаем: признак «бренд в домене», по которому
    раньше снимали, для нутры не работает — она льёт с клоак-доменов, и автоснятие
    убрало с сетки лучший ключ (Adenofrin/ES). Снимать — руками в админке.
    """
    from sqlalchemy import text
    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(text("""
                UPDATE parsing_configs c SET max_collect = true
                WHERE c.config_type = 'keyword' AND c.is_active
                  AND NOT c.max_collect AND c.keyword IS NOT NULL
                  AND (SELECT count(*) FROM ads a
                        WHERE lower(a.keyword) = lower(c.keyword)
                          AND upper(a.country) = upper(c.country)) >= :porog
                RETURNING c.id, c.keyword, c.country
            """), {"porog": _POROG_SETKI})).all()
            if rows:
                await session.commit()
                for cid, kw, co in rows:
                    logger.info(
                        f"[coordinator] #{cid} {kw}/{co}: набрал объём — "
                        f"перевожу на сбор сеткой ({len(_MAX_LANGS)}x{len(_MAX_STATUS)}"
                        f"x{len(_MEDIA_SEGMENTS)} срезов вместо двух)"
                    )
            return len(rows)
    except Exception as exc:
        logger.warning(f"[coordinator] авто-подключение сетки не сработало: {exc}")
        return 0


async def enqueue_run(run_id: int, mode_override: str | None = None) -> list[str]:
    """Enqueue one deduped RQ 'parse' job per (config, date-chunk). Returns job ids."""
    from app.queue import parse_queue
    from app.tasks import parse_chunk
    from rq import Retry
    from rq.job import Job

    q = parse_queue()
    conn = q.connection
    # Перед планированием убираем хвосты мёртвых прогонов — иначе новые срезы
    # встают в очередь за тысячами зомби-заданий и прогон не начинается.
    await _pochistit_ochered(q, conn, run_id)
    # ОТКЛЮЧЕНО ПО ТРЕБОВАНИЮ: сбор должен быть одинаковым для всех ключей, без
    # скрытых переключений. Автоматика включала сетку по объёму — «ключ дал много,
    # значит есть что брать», — но объём не отличает рекламу бренда от чужой рекламы
    # со словом внутри. По «Grow» это дало 18 444 вебновелл и видеочатов за прогон:
    # слово сидит в чужих доменах вроде u4grow.com, а сетка размножила его в 144 среза.
    # Глубокий сбор остаётся, но включается вручную галочкой max_collect в админке.
    # await _podklyuchit_setku()
    job_ids: list[str] = []
    skipped_done = 0
    resumed = 0

    async with AsyncSessionLocal() as session:
        run = await session.get(ParserRun, run_id)
        # mode_override — для «подхватить <тип>» (reload); иначе берём режим самого рана.
        mode = mode_override or (getattr(run, "mode", None) if run else None)  # keyword / filters / all
        configs = await get_active_configs(session, mode=mode)

        # Адресный прогон: если в ране перечислены конкретные конфиги — берём только их.
        # Иначе «собрать 5 мая по США» разворачивало ВСЕ активные гео (16 конфигов),
        # и заказчик получал совсем не то, что просил.
        _only = (getattr(run, "only_configs", None) or "").strip() if run else ""
        if _only:
            _wanted = {int(x) for x in _only.split(",") if x.strip().isdigit()}
            configs = [c for c in configs if c.id in _wanted]
            logger.info(f"[coordinator] адресный прогон: только конфиги {sorted(_wanted)}")

        for _c in configs:
            _GEO_BY_CONFIG[_c.id] = (getattr(_c, 'country', None) or '?')

        for config in configs:
            # ── Посуточный конфиг в широком прогоне пропускаем ──
            # Его создаёт кнопка «Запуск за день» и запускает АДРЕСНО (only_configs).
            # Оставшись активным, он попадал и в обычный прогон по фильтрам: конфиг
            # #218 US с целевым днём 21 августа ставил 5 160 срезов из 5 205, то есть
            # каждый прогон заново перемалывал один давно собранный день и не давал
            # дойти до остальных пятнадцати гео (у них на всех 45 срезов).
            if ((getattr(config, "sort_mode", "") or "") == _DAY_SWEEP_MODE
                    and config.config_type in ("filters", "fanpage")
                    and not _only):
                logger.info(
                    f"[coordinator] #{config.id} {getattr(config, 'country', '?')}: "
                    f"посуточный конфиг вне адресного запуска — пропускаю "
                    f"(целевой день {getattr(config, 'date_to', None)}); "
                    f"чтобы собрать день, жмите «Запуск за день»"
                )
                continue

            # ── Media-сегментация для filters/fanpage ──
            # Вместо лоссовых узких date-окон (start_date[min] недобирает в разы) гоним
            # НЕСКОЛЬКО ШИРОКИХ срезов по типу медиа (image/video/meme) — каждый на своём
            # воркере ПАРАЛЛЕЛЬНО, весь диапазон дат ([max]=date_to, без [min]). upsert по
            # library_id дедуплит пересечения. Даёт и параллелизм, и полноту. Резюма нет
            # (track_chunk=False на media-джобах) — прерывание безопасно (upsert идемпотентен).
            if config.config_type in ("filters", "fanpage"):
                df = min(config.date_from or date(2019, 1, 1), date(2019, 1, 1))
                # Именно здесь ветка фильтров считает свои даты — мимо _chunks_for.
                dt = _okno_do_segodnya(config, config.date_to)
                # ── Сбор «за конкретный день» ──
                # Конфиг с sort_mode=relevancy_monthly_grouped означает: собрать рекламу,
                # ЗАПУЩЕННУЮ в день (date_to - 1). FB при этом ранжировании отдаёт самое
                # свежее под отсечкой, поэтому ~95% выдачи каждого среза — нужный день.
                # Глубина: max_collect=false — только базовая сетка (быстрая проба,
                # ~15 мин), true — плюс перебор по проверенным словам (полный сбор).
                if (getattr(config, "sort_mode", "") or "") == _DAY_SWEEP_MODE:
                    # date_to у такого конфига = САМ целевой день, а не отсечка.
                    # Отсечку (день + 1) считаем здесь, в одном месте: раньше её
                    # выставляли снаружи, и человек, вписывая нужную дату, стабильно
                    # промахивался на сутки назад.
                    _cutoff = dt + timedelta(days=1) if dt else None
                    _slovar = _proven_words() if getattr(config, "max_collect", False) else []
                    if _SMART_SWEEP:
                        _istoriya = await _slova_geo(session, getattr(config, "country", "") or "")
                        _rodnye = await _rodnye_slova(session, _yazyki_geo(config))
                        _plan = _plan_dnya(config, _istoriya, _slovar, _rodnye, den=dt)
                        logger.info(
                            f"[coordinator] #{config.id} {config.country}: умный план — "
                            f"{len(_plan)} срезов (слов с историей {len(_istoriya)}, "
                            f"родных слов {sum(len(v) for v in _rodnye.values())} "
                            f"по {len(_rodnye)} языкам, словарь {len(_slovar)})"
                        )
                    else:
                        _plan = [{"tag": f"{st}_{mt}", "active_status": st, "media_type": mt,
                                  "max_ads": _DAY_SWEEP_CAP}
                                 for st in _MAX_STATUS for mt in _MEDIA_SEGMENTS]
                        _plan += [{"tag": "kw-" + "".join(c if c.isalnum() else "-" for c in w.lower()),
                                   "active_status": st, "keyword": w, "max_ads": _DAY_SWEEP_CAP}
                                  for w in _slovar for st in _MAX_STATUS]

                    for _srez in _plan:
                        _tag = _srez.pop("tag")
                        jid = f"day_{config.id}_{_tag}_{run_id}"
                        if Job.exists(jid, connection=conn):
                            continue
                        kwargs = {
                            "config_id": config.id,
                            "date_from": _iso(df),
                            "date_to": _iso(_cutoff),
                            "run_id": run_id,
                            # строго один день: чужие даты не сохраняем
                            "only_day": _iso(dt),
                            **_srez,
                        }
                        q.enqueue(parse_chunk, kwargs=kwargs, job_id=jid,
                                  job_timeout=settings.parse_job_timeout,
                                  result_ttl=3600, failure_ttl=86400,
                                  retry=Retry(max=2, interval=[180, 300]))
                        job_ids.append(jid)
                    logger.info(
                        f"[coordinator] #{config.id} {config.country}: целевой день {dt} "
                        f"(отсечка {_cutoff}), поставлено {len(job_ids)} срезов"
                    )
                    continue

                if getattr(config, "max_collect", False):
                    for lang in _MAX_LANGS:
                        for st in _MAX_STATUS:
                            for mt in _MEDIA_SEGMENTS:
                                jid = f"max_{config.id}_{st}_{mt}_{lang}_{run_id}"
                                if Job.exists(jid, connection=conn):
                                    continue
                                q.enqueue(
                                    parse_chunk,
                                    kwargs={
                                        "config_id": config.id,
                                        "date_from": _iso(df),
                                        "date_to": _iso(dt),
                                        "media_type": mt,
                                        "languages": [lang],
                                        "active_status": st,
                                        "run_id": run_id,
                                    },
                                    job_id=jid,
                                    job_timeout=settings.parse_job_timeout,
                                    result_ttl=3600,
                                    failure_ttl=86400,
                                    retry=Retry(max=3, interval=[60, 180, 300]),
                                )
                                job_ids.append(jid)
                    logger.info(
                        f"[coordinator] #{config.id} {config.country}: максимальный сбор — "
                        f"{len(_MAX_LANGS) * len(_MAX_STATUS) * len(_MEDIA_SEGMENTS)} срезов "
                        f"(язык x статус x медиа)"
                    )
                    continue
                for mt in _MEDIA_SEGMENTS:
                    jid = f"chunk_{config.id}_{mt}_{run_id}"
                    if Job.exists(jid, connection=conn):
                        continue
                    q.enqueue(
                        parse_chunk,
                        kwargs={
                            "config_id": config.id,
                            "date_from": _iso(df),
                            "date_to": _iso(dt),
                            "media_type": mt,
                            "run_id": run_id,
                        },
                        job_id=jid,
                        job_timeout=settings.parse_job_timeout,
                        result_ttl=3600,
                        failure_ttl=86400,
                        retry=Retry(max=3, interval=[60, 180, 300]),
                    )
                    job_ids.append(jid)
                continue
            # ── Бездонный ключ: собираем сеткой, а не одним последовательным срезом ──
            # FB режет КАЖДЫЙ набор параметров и отдаёт по 10 карточек за запрос, поэтому
            # один срез на одном воркере такой ключ не исчерпает никогда: DiaStop/UZ за
            # шесть часов взял 8 602 карточки и остался с has_next=true. Узкие срезы
            # почти не пересекаются — замер 02.09 по DiaStop/UZ: восемь срезов дали 348
            # уникальных против 80 у несегментированного, множитель x4.3. Сетка уходит
            # на 48 воркеров параллельно.
            if config.config_type == "keyword" and getattr(config, "max_collect", False):
                _df_kw, _dt_kw = next(iter(_chunks_for(config)), (None, None))
                _postavleno = 0
                for lang in _MAX_LANGS:
                    for st in _MAX_STATUS:
                        for mt in _MEDIA_SEGMENTS:
                            jid = f"kwmax_{config.id}_{st}_{mt}_{lang}_{run_id}"
                            if Job.exists(jid, connection=conn):
                                continue
                            q.enqueue(
                                parse_chunk,
                                kwargs={
                                    "config_id": config.id,
                                    "date_from": _iso(_df_kw),
                                    "date_to": _iso(_dt_kw),
                                    "media_type": mt,
                                    "languages": [lang],
                                    "active_status": st,
                                    "run_id": run_id,
                                },
                                job_id=jid,
                                job_timeout=settings.parse_job_timeout,
                                result_ttl=3600,
                                failure_ttl=86400,
                                retry=Retry(max=3, interval=[60, 180, 300]),
                            )
                            job_ids.append(jid)
                            _postavleno += 1
                logger.info(
                    f"[coordinator] #{config.id} {config.keyword}/{config.country}: "
                    f"бездонный ключ — сетка из {_postavleno} срезов "
                    f"({len(_MAX_LANGS)} языков x {len(_MAX_STATUS)} статуса x {len(_MEDIA_SEGMENTS)} медиа)"
                )
                continue

            # ── keyword и прочие: обычный date-chunk путь (одно окно, резюмируемое) ──
            for (df, dt) in _chunks_for(config):
                # Consult the resume bookmark: skip exhausted chunks, resume in-progress
                # ones from their saved cursor, start fresh ones from the first page.
                kdf, kdt = chunk_key(df, dt)
                progress = await session.get(ChunkProgress, (config.id, kdf, kdt, ""))
                if progress is not None and not progress.has_next:
                    # Пропускаем, только если закладку поставил ЭТОТ ЖЕ прогон:
                    # защита нужна от повтора задания внутри одного запуска
                    # (воркер упал → RQ повторил), а не от нового запуска.
                    # Со сроком в часах человек жал кнопку и не собирал ничего.
                    if getattr(progress, "last_run_id", None) == run_id:
                        skipped_done += 1
                        continue
                cursor_start = progress.last_cursor if progress else None
                if cursor_start:
                    resumed += 1

                # Deterministic id → re-triggering a run doesn't duplicate in-flight chunks.
                # Colon-free so it can't collide with RQ's `rq:job:<id>` Redis key namespace
                # (baked in here so no post-pull sed is needed — that sed also ate the colon
                # in `track_chunk:` annotations and broke worker.py).
                # Ключ ищем ДВАЖДЫ: вразброс (объём) и точной фразой (релевантность).
                # Раньше фраза включалась только для многословных ключей, и это
                # теряло бренды: FB ищет со стеммингом, поэтому по «ProstaMen» в
                # Польше выдача забита словом «prosta» (простой) — 3 300 карточек,
                # из первых тридцати НИ ОДНОЙ про бренд. Точная фраза на том же
                # ключе дала 30 карточек, 19 релевантных, с посадочными вида
                # freshyouessence.com/fb-pl-prostamen. Замер 02.09.
                # Поэтому точную фразу гоняем ВСЕГДА, где есть ключ.
                _rezhimy = [None]
                if (getattr(config, "keyword", "") or "").strip():
                    _rezhimy.append("keyword_exact_phrase")

                for _st in _rezhimy:
                    jid = (f"chunk_{config.id}_{df}_{dt}_{run_id}" if _st is None
                           else f"chunk_{config.id}_{df}_{dt}_fraza_{run_id}")
                    if Job.exists(jid, connection=conn):
                        continue
                    _kwargs = {
                        "config_id": config.id,
                        "date_from": _iso(df),
                        "date_to": _iso(dt),
                        "cursor_start": cursor_start,
                        "run_id": run_id,
                    }
                    if _st:
                        _kwargs["search_type"] = _st
                    q.enqueue(
                        parse_chunk,
                        kwargs=_kwargs,
                        job_id=jid,
                        job_timeout=settings.parse_job_timeout,
                        result_ttl=3600,
                        failure_ttl=86400,
                        # A GraphQL failure now fails the chunk (no silent DOM-scroll
                        # fallback). Retry with growing backoff; parse_chunk rotates IP
                        # before each retry.
                        retry=Retry(max=3, interval=[60, 180, 300]),
                    )
                    job_ids.append(jid)

    logger.info(
        f"[coordinator] run #{run_id}: enqueued {len(job_ids)} chunk job(s) "
        f"({resumed} resumed, {skipped_done} skipped as exhausted) across {len(configs)} config(s)"
    )
    return job_ids


async def _obyavleniy_za_progon(run_id: int) -> int:
    """Сколько объявлений легло в базу с начала прогона — прямым счётом.

    Счётчики в строке прогресса складываются из ЗАКРЫТЫХ срезов, а у медиа-,
    языковых и статусных срезов закладки нет вовсе (track_chunk=False). Поэтому
    прогресс показывал new=0 при тысячах реально собранных объявлений: 02.09
    строка держала new=453, когда в базу уже упало 44 078. Человек, глядя на такое,
    решает, что сбор не работает, и жмёт «Отменить».
    """
    from sqlalchemy import text
    try:
        async with AsyncSessionLocal() as session:
            return (await session.execute(text(
                "SELECT count(*) FROM ads WHERE first_seen_at >= "
                "(SELECT coalesce(started_at, triggered_at) FROM parser_runs WHERE id = :r)"),
                {"r": run_id})).scalar() or 0
    except Exception:
        return -1


async def monitor_run(run_id: int, job_ids: list[str], poll_sec: int = 5) -> dict:
    """Poll chunk jobs until all terminal (or run cancelled); aggregate their stats.

    A crashed/OOM-killed chunk (its work-horse dies without marking the job failed) used
    to leave the job stuck in 'started' forever, hanging the whole coordinator. Each poll
    we advance abandoned/timed-out jobs into the FailedJobRegistry and count anything there
    (or a vanished job) as a failed chunk, so one bad chunk can't block the run.
    """
    from app.queue import parse_queue
    from rq.job import Job
    from rq.registry import FailedJobRegistry, StartedJobRegistry

    q = parse_queue()
    conn = q.connection
    failed_reg = FailedJobRegistry(queue=q)
    started_reg = StartedJobRegistry(queue=q)
    total = {k: 0 for k in _STAT_KEYS}
    # Chunk-outcome counters kept SEPARATE from card-level `errors`: a failed chunk is one
    # unit, not one "error", so "7/21 done" can no longer secretly mean "7 died".
    total["chunks_done"] = 0    # jobs that finished successfully
    total["chunks_failed"] = 0  # jobs that failed terminally / vanished
    pending = set(job_ids)
    last_report = (-1, -1)      # (done, failed) — re-log when either moves
    progress_seen: dict = {}   # chunk key -> collected_count last logged (delta detection)
    started_seen: set = set()  # chunks we've already logged a first "collecting" line for
    polls = 0

    while pending:
        await asyncio.sleep(poll_sec)
        polls += 1

        # Graceful shutdown (SIGTERM): persist what we have and stop monitoring. Jobs keep
        # running in the workers and resume from chunk_progress on the next run.
        if shutdown_event.is_set():
            await _persist_run_stats(run_id, total)
            logger.info(f"[coordinator] run #{run_id}: shutdown requested — stats persisted, monitor stopping")
            return total

        # A single bad poll (Redis hiccup, transient fetch error) must not silently kill the
        # monitor loop — that's the "0/33 chunks done then silence for hours" symptom.
        try:
            # Honour admin cancel: stop pending jobs and bail (keeping stats).
            async with AsyncSessionLocal() as session:
                run = await session.get(ParserRun, run_id)
            if run and run.status == "cancelled":
                for jid in list(pending):
                    try:
                        Job.fetch(jid, connection=conn).cancel()
                    except Exception:
                        pass
                await _persist_run_stats(run_id, total)
                logger.info(f"[coordinator] run #{run_id} cancelled — stopped {len(pending)} pending job(s)")
                return total

            # «Подхватить <тип>»: админка выставила reload_mode ('keyword'|'filters'|'all') →
            # до-enqueue'им новые активные конфиги ЭТОГО типа на лету (можно и тип, отличный от
            # режима рана — так «подхватить фильтры» расширит keyword-ран). enqueue_run сам
            # пропустит уже стоящие/исчерпанные чанки, вернёт только реально новые джобы.
            reload_mode = getattr(run, "reload_mode", None) if run else None
            if reload_mode:
                try:
                    new_ids = await enqueue_run(run_id, mode_override=reload_mode)
                except Exception as exc:
                    new_ids = []
                    logger.warning(f"[coordinator] run #{run_id}: reload enqueue failed: {exc}")
                if new_ids:
                    pending.update(new_ids)
                async with AsyncSessionLocal() as session:
                    r2 = await session.get(ParserRun, run_id)
                    if r2:
                        r2.reload_mode = None
                        await session.commit()
                logger.info(
                    f"[coordinator] run #{run_id}: подхвачено {len(new_ids)} новых чанк-джоб "
                    f"(reload тип={reload_mode})"
                )

            # Move jobs whose work-horse died (OOM / timeout / crash) out of 'started' so we
            # don't wait on them forever; then treat FailedJobRegistry membership as terminal.
            try:
                started_reg.cleanup()
                failed_reg.cleanup()
                failed_ids = set(failed_reg.get_job_ids())
            except Exception as exc:
                logger.warning(f"[coordinator] registry cleanup failed: {exc}")
                failed_ids = set()

            # Fetch each pending job once; reuse for both status and progress logging.
            jobs: dict = {}
            for jid in list(pending):
                try:
                    jobs[jid] = Job.fetch(jid, connection=conn)
                except Exception:
                    # Job data expired/gone — stop waiting, count it as a failed chunk.
                    logger.warning(f"[coordinator] job {jid} vanished — treating as failed")
                    total["chunks_failed"] += 1
                    pending.discard(jid)

            retrying = 0  # jobs waiting on a scheduled Retry (not yet terminal, not stuck)
            for jid, job in jobs.items():
                if jid not in pending:
                    continue
                kw = job.kwargs or {}
                label = _chunk_label(kw.get("config_id"), kw.get("date_from"), kw.get("date_to"))
                status = job.get_status(refresh=True)
                if jid in failed_ids or status == "failed":
                    total["chunks_failed"] += 1
                    tail = (job.exc_info or "")[-300:]
                    logger.warning(f"[coordinator] chunk {label} FAILED: {tail}")
                    pending.discard(jid)
                    continue
                if status in _TERMINAL:
                    # finished (aggregate + log a real summary) or canceled/stopped (just drop).
                    if status == "finished" and isinstance(job.result, dict):
                        r = job.result
                        for k, v in r.items():
                            if isinstance(v, int):
                                total[k] = total.get(k, 0) + v
                        total["chunks_done"] += 1
                        if r.get("skipped_zombie"):
                            # A no-op skip (exhausted day / stale run) — don't clobber the
                            # chunk's real last_stats with a placeholder.
                            logger.info(f"[coordinator] chunk {label} skipped (zombie/exhausted)")
                        else:
                            logger.info(
                                f"[coordinator] chunk {label} done: raw={r.get('raw', 0)} "
                                f"new={r.get('new', 0)} updated={r.get('updated', 0)} "
                                f"skipped_reviewed={r.get('skipped_already_reviewed', 0)} "
                                f"errors={r.get('errors', 0)}"
                            )
                            # Persist the per-chunk totals to the DB (survives log wipes).
                            await _persist_chunk_result(
                                run_id, kw.get("config_id"), kw.get("date_from"), kw.get("date_to"), r
                            )
                            # ОТКЛЮЧЕНО: сбор одинаков для всех ключей, без скрытых
                            # переключений. Эта ветка включала сетку, когда обычный срез
                            # набрал больше 2 000 карточек, и втихую вернула DiaStop/UZ
                            # на сетку уже ПОСЛЕ того, как автоматику отключили в другом
                            # месте. Вторая такая же ветка — _podklyuchit_setku в
                            # enqueue_run. Глубокий сбор включается только вручную,
                            # галочкой max_collect в админке.
                            # await _pometit_bezdonnym(
                            #     kw.get("config_id"), kw.get("date_from"), kw.get("date_to")
                            # )
                    pending.discard(jid)
                elif status in ("scheduled", "deferred"):
                    retrying += 1  # a failed attempt is queued to retry with backoff

            # Surface live collection (committed batches, chunk starts) from chunk_progress.
            coords = []
            for jid in pending:
                job = jobs.get(jid)
                if job is None:
                    continue
                kw = job.kwargs or {}
                cid = kw.get("config_id")
                if cid is None:
                    continue
                kdf, kdt = chunk_key(_iso_to_date(kw.get("date_from")), _iso_to_date(kw.get("date_to")))
                coords.append((cid, kdf, kdt, _chunk_label(cid, kw.get("date_from"), kw.get("date_to"))))
            await _log_chunk_progress(coords, progress_seen, started_seen)

            # Persist the aggregate incrementally so cancel/crash/kill keeps the numbers.
            await _persist_run_stats(run_id, total)

            done = total["chunks_done"]
            failed = total["chunks_failed"]
            if (done, failed) != last_report:
                logger.info(
                    f"[coordinator] run #{run_id}: {done} done, {failed} failed, "
                    f"{retrying} retrying, {len(pending)} pending / {len(job_ids)} chunks"
                )
                last_report = (done, failed)
            elif polls % 12 == 0:
                # Heartbeat (~once a minute at poll_sec=5) so the monitor is never silent for
                # hours — makes a genuine stall visible instead of looking like a dead loop.
                # «в базе» — прямой счёт, а не сумма по закрытым срезам: у сегментированных
                # срезов закладки нет, и без этой цифры прогресс выглядит замершим.
                _v_baze = await _obyavleniy_za_progon(run_id)
                logger.info(
                    f"[coordinator] run #{run_id}: {done} done, {failed} failed, {retrying} retrying, "
                    f"{len(pending)} pending (по закрытым срезам new={total['new']} raw={total['raw']}; "
                    f"в базе за прогон {_v_baze})"
                )
        except Exception as exc:
            logger.warning(f"[coordinator] run #{run_id}: monitor poll error (continuing): {exc}")

    await _persist_run_stats(run_id, total)
    logger.info(f"[coordinator] run #{run_id} complete: {total}")
    return total


# Сколько объявлений за прогон должен дать ключ, чтобы имело смысл проверять
# его на «обычное слово». Ниже порога доля по доменам — шум.
_POROG_PROVERKI_KACHESTVA = int(os.getenv("POROG_PROVERKI_KACHESTVA", "500") or 500)
# Минимальная доля объявлений, где ключ стоит ОТДЕЛЬНЫМ куском домена посадочной
# (idealfit.fit), а не внутри чужого (u4grow.com). Ниже — ключ ловит чужую рекламу.
_MIN_DOLYA_BRENDA = int(os.getenv("MIN_DOLYA_BRENDA", "10") or 10)


async def _snyat_s_setki(run_id: int) -> None:
    """Снять с сетки ключи, которые оказались обычными словами.

    Сетка усиливает то, что ключ приносит. У «IdealFit» это настоящий бренд —
    99% собранного ведёт на idealfit.fit. У «Grow» это английское слово: 2 695
    объявлений за прогон, и лишь 5% несут ключ отдельным куском домена (остальное
    u4grow.com, grow-with-knowledge.org, «Grow & Learn» — игры, дейтинг, курсы).
    Без этой проверки сетка множит мусор в тысячи раз. Проверяем только объёмные
    ключи: на десятке объявлений доля по доменам ничего не значит.
    """
    from sqlalchemy import text
    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(text("""
                WITH sobrano AS (
                    SELECT c.id, c.keyword, c.country,
                           count(*) AS novyh,
                           count(*) FILTER (
                               WHERE lower(coalesce(a.link_url, a.display_url, '')) ~
                                     ('(^|[./_-])' || lower(regexp_replace(c.keyword, '[^a-zA-Z0-9]', '', 'g'))
                                      || '([./_-]|$)')
                           ) AS s_brendom
                    FROM parsing_configs c
                    JOIN ads a ON lower(a.keyword) = lower(c.keyword)
                              AND upper(a.country) = upper(c.country)
                    WHERE c.max_collect AND c.config_type = 'keyword' AND c.keyword IS NOT NULL
                      AND a.first_seen_at >= (SELECT coalesce(started_at, triggered_at)
                                                FROM parser_runs WHERE id = :rid)
                    GROUP BY c.id, c.keyword, c.country
                )
                SELECT id, keyword, country, novyh, s_brendom FROM sobrano
                WHERE novyh >= :porog
                  AND (100 * s_brendom / novyh) < :dolya
            """), {"rid": run_id, "porog": _POROG_PROVERKI_KACHESTVA,
                   "dolya": _MIN_DOLYA_BRENDA})).all()

            for cid, kw, country, novyh, s_brendom in rows:
                cfg = await session.get(ParsingConfig, cid)
                if cfg is None:
                    continue
                cfg.max_collect = False
                dolya = 100 * s_brendom // novyh if novyh else 0
                logger.warning(
                    f"[coordinator] #{cid} {kw}/{country}: за прогон {novyh} объявлений, "
                    f"но лишь {dolya}% несут ключ в домене посадочной — ключ ведёт себя "
                    f"как обычное слово, снимаю с сетки (иначе она множит чужую рекламу)"
                )
            if rows:
                await session.commit()
    except Exception as exc:
        logger.warning(f"[coordinator] проверка качества ключей не прошла: {exc}")


# Ссылки на фоновые задачи: без них сборщик мусора может убить задачу на полпути.
_FONOVYE: set = set()


def _konfigi_progona(job_ids: list[str]) -> list[int]:
    """Номера конфигов из id джоб прогона: chunk_<id>_..., kwmax_<id>_..., max_<id>_..., day_<id>_..."""
    nomera = set()
    for jid in job_ids:
        chasti = jid.split("_")
        if len(chasti) > 1 and chasti[1].isdigit():
            nomera.add(int(chasti[1]))
    return sorted(nomera)


async def run_discovery_via_queue(run_id: int) -> dict:
    """Enqueue + monitor a discovery run. Drop-in replacement for worker.run_once()."""
    job_ids = await enqueue_run(run_id)
    if not job_ids:
        logger.warning(f"[coordinator] run #{run_id}: no active configs / no jobs enqueued")
        return {k: 0 for k in _STAT_KEYS}
    # Размер плана сохраняем сразу: без него у остановленного прогона не отличить
    # «сломался» от «остановили осознанно, план выполнен на 96%», и каждый раз
    # приходится объяснять это словами.
    try:
        async with AsyncSessionLocal() as _s:
            _r = await _s.get(ParserRun, run_id)
            if _r is not None:
                _st = dict(_r.stats or {})
                _st["chunks_planned"] = len(job_ids)
                _r.stats = _st
                await _s.commit()
    except Exception as _e:
        logger.warning(f"[coordinator] не сохранил размер плана: {_e}")

    itogo = await monitor_run(run_id, job_ids)
    # ОТКЛЮЧЕНО: признак «бренд в домене посадочной» для нутры не работает —
    # она льёт с клоак-доменов (adinofry.medwayrule.com, ae.cdo915.store), и доля
    # брендовых доменов у настоящего бренда такая же низкая, как у обычного слова:
    # Adenofrin/ES 2%, Grow/UZ 3%. Из-за этого автоснятие убрало с сетки лучший ключ
    # сессии — Adenofrin/ES, выросший с 569 объявлений до 2 928. Пока нет признака,
    # который надёжно отличает бренд от обычного слова, решение принимает человек:
    # сетка включается и снимается вручную в админке.
    # await _snyat_s_setki(run_id)
    # Счётчик FB снимаем в конце прогона: без него разрыв «в витрине 52, а на FB
    # 3 300» выглядит недосбором, хотя это стемминг («prosta» вместо «ProstaMen»).
    # ФОНОМ и НЕ ДОЖИДАЯСЬ: обход браузером ста сорока ключей занимает четверть часа,
    # и если его ждать, прогон висит в running и блокирует следующий запуск.
    # Ошибка здесь ничего не стоит: данные уже собраны, счётчик — справочная величина.
    try:
        from app.fb_schetchik import obnovit_schetchiki
        _zadacha = asyncio.create_task(obnovit_schetchiki(_konfigi_progona(job_ids)))
        _FONOVYE.add(_zadacha)
        _zadacha.add_done_callback(_FONOVYE.discard)
    except Exception as exc:
        logger.warning(f"[coordinator] счётчики FB не запустились: {type(exc).__name__}: {exc}")
    return itogo
