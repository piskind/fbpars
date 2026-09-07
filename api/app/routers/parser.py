from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.deps import get_current_admin
from app.models_proxy import ParserRun, ParsingConfig
from app.schemas import ParserRunOut, ParserStatusOut, ParserStartIn, ParserReloadIn

router = APIRouter(prefix="/api/parser", tags=["parser"])

# Незакрытые статусы + допустимые режимы запуска.
_ACTIVE = ["triggered", "running"]
_VALID_MODES = {"keyword", "filters", "all"}


async def _active_run(session: AsyncSession) -> ParserRun | None:
    """Последний незакрытый ран (running/triggered).

    ВАЖНО: через .first(), а НЕ scalar_one_or_none() — незакрытых ранов легко бывает
    несколько (очередь triggered), и scalar_one_or_none падал с 'Multiple rows found'.
    Это и был баг, из-за которого /start и /cancel отваливались.
    """
    return (await session.execute(
        select(ParserRun)
        .where(ParserRun.status.in_(_ACTIVE))
        .order_by(ParserRun.triggered_at.desc())
        .limit(1)
    )).scalars().first()


@router.get("/status", response_model=ParserStatusOut)
async def parser_status(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    try:
        recent = list((await session.execute(
            select(ParserRun).order_by(ParserRun.triggered_at.desc()).limit(10)
        )).scalars().all())
    except Exception:
        return ParserStatusOut(running=False, last_run=None, recent_runs=[])

    running_run = next((r for r in recent if r.status in _ACTIVE), None)
    last_run = next((r for r in recent if r.status in ("done", "failed")), None)

    async def _count(*conds) -> int:
        return (await session.execute(
            select(func.count()).select_from(ParsingConfig)
            .where(ParsingConfig.is_active.is_(True), *conds)
        )).scalar() or 0

    try:
        active_keyword = await _count(ParsingConfig.config_type == "keyword")
        active_filters = await _count(ParsingConfig.config_type.in_(["filters", "fanpage"]))
    except Exception:
        active_keyword = active_filters = 0

    return ParserStatusOut(
        running=running_run is not None,
        current_mode=running_run.mode if running_run else None,
        last_run=last_run,
        recent_runs=recent,
        active_keyword=active_keyword,
        active_filters=active_filters,
    )


@router.post("/start", response_model=ParserRunOut)
async def start_parser(
    payload: ParserStartIn | None = None,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    """Запустить прогон в выбранном режиме: keyword / filters / all.

    Рестарт контейнера НЕ нужен — координатор поднимает triggered-ран поллером за ~15с.
    """
    mode = (payload.mode if payload else "all") or "all"
    if mode not in _VALID_MODES:
        raise HTTPException(status_code=422, detail=f"Неизвестный режим: {mode}")

    try:
        active = await _active_run(session)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB unavailable: {e}")

    if active:
        raise HTTPException(status_code=409, detail="Парсер уже запущен или ожидает запуска")

    run = ParserRun(status="triggered", mode=mode)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


@router.post("/reload", response_model=ParserRunOut)
async def reload_configs(
    payload: ParserReloadIn | None = None,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    """«Подхватить <тип>»: до-собрать только что включённые конфиги указанного типа
    (keyword / filters / all) без остановки прогона. Ставим reload_mode — координатор на
    следующей итерации до-enqueue'ит новые конфиги этого типа в текущий ран."""
    mode = (payload.mode if payload else "all") or "all"
    if mode not in _VALID_MODES:
        raise HTTPException(status_code=422, detail=f"Неизвестный тип: {mode}")

    active = await _active_run(session)
    if not active:
        raise HTTPException(status_code=404, detail="Нет активного запуска — подхватывать нечего")
    active.reload_mode = mode
    await session.commit()
    await session.refresh(active)
    return active


@router.post("/cancel", response_model=ParserRunOut)
async def cancel_parser(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    """Отменить сбор: гасим ВСЕ незакрытые раны (и running, и очередь triggered)."""
    try:
        actives = list((await session.execute(
            select(ParserRun)
            .where(ParserRun.status.in_(_ACTIVE))
            .order_by(ParserRun.triggered_at.desc())
        )).scalars().all())
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB unavailable: {e}")

    if not actives:
        raise HTTPException(status_code=404, detail="Нет активных запусков")

    now = datetime.now(timezone.utc)
    for a in actives:
        a.status = "cancelled"
        a.finished_at = now
    await session.commit()
    await session.refresh(actives[0])
    return actives[0]


# Сколько объявлений должно быть в базе, чтобы ноль за прогон считать подозрительным.
# Ниже порога вердикт только шумел: у Big Hunter/IN в базе 2 объявления, у
# Osteoguard/KE одно — ноль по таким ключам ничего не означает, а в отчёте они
# висели красным рядом с настоящими случаями. Проверено: срезы отработали все
# шесть попыток со сменой канала, FB отдал пусто — это не троттлинг.
_POROG_PODOZRENIYA = 20


@router.get("/otchet/{run_id}", response_model=dict)
async def otchet_progona(
    run_id: int,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    """Сводка по прогону: что собрано, где пусто и где ноль подозрительный.

    Считается по slice_events за окно прогона, поэтому видит ВСЕ срезы конфига,
    а не первый попавшийся. Это принципиально: у многословных ключей срезов два
    (вразброс и точной фразой), и первый может закрыться нулём, пока второй
    приносит выдачу — так Hammer of Thor/EG в прогоне 318 дал 0 в 07:10 и 15 505
    в 10:32. Глазами такое не ловится, отчётом ловится.
    """
    run = (await session.execute(text(
        "SELECT id, status, mode, coalesce(started_at, triggered_at), "
        "       coalesce(finished_at, now()), stats "
        "FROM parser_runs WHERE id = :id"), {"id": run_id})).first()
    if not run:
        raise HTTPException(status_code=404, detail="Прогон не найден")
    _, status, mode, ot, do_, _stats = run
    # Выполнение плана: без него остановленный вручную прогон неотличим от упавшего,
    # и каждый раз приходится объяснять словами, что 96% плана — это не авария.
    _stats = _stats or {}
    _v_plane = int(_stats.get("chunks_planned") or 0)
    _sdelano = int(_stats.get("chunks_done") or 0)
    _procent = round(100.0 * _sdelano / _v_plane, 1) if _v_plane else None

    rows = (await session.execute(text("""
        WITH srezy AS (
            SELECT s.config_id,
                   count(*)                            AS srezov,
                   count(*) FILTER (WHERE s.vsego > 0) AS s_vydachey,
                   coalesce(sum(s.vsego), 0)           AS kartochek,
                   coalesce(sum(s.novyh), 0)           AS novyh,
                   coalesce(max(s.sekund), 0)          AS sekund
            FROM slice_events s
            -- Привязка по номеру прогона; окно по времени оставлено только для
            -- старых строк (до появления slice_events.run_id), иначе срез,
            -- закрывшийся уже после старта следующего прогона, приписывался ему.
            WHERE s.run_id = :rid
               OR (s.run_id IS NULL AND s.finished_at >= :ot AND s.finished_at <= :do)
            GROUP BY 1
        )
        SELECT c.id, c.keyword, c.country, c.config_type, c.max_collect,
               z.srezov, z.s_vydachey, z.kartochek, z.novyh, z.sekund,
               (SELECT count(*) FROM ads a
                 WHERE lower(a.keyword) = lower(c.keyword)
                   AND upper(a.country) = upper(c.country)) AS v_baze,
               c.fb_schetchik, c.fb_schetchik_at
        FROM srezy z JOIN parsing_configs c ON c.id = z.config_id
        ORDER BY z.kartochek DESC
    """), {"ot": ot, "do": do_, "rid": run_id})).all()

    konfigi = []
    itogo = {"konfigov": 0, "srezov": 0, "kartochek": 0, "novyh": 0,
             "pusto_u_fb": 0, "podozritelnyh": 0}
    for (cid, kw, country, ctype, setka, srezov, s_vyd, kart, novyh, sek, v_baze,
         fb_schet, fb_schet_at) in rows:
        if kart > 0:
            verdikt = "собрано"
        elif (v_baze or 0) >= _POROG_PODOZRENIYA:
            verdikt = "подозрительный ноль"
            itogo["podozritelnyh"] += 1
        else:
            verdikt = "пусто у FB"
            itogo["pusto_u_fb"] += 1
        konfigi.append({
            "config_id": cid, "keyword": kw, "country": country,
            "tip": ctype, "setka": bool(setka),
            "srezov": srezov, "srezov_s_vydachey": s_vyd,
            "kartochek": int(kart), "novyh": int(novyh), "sekund": int(sek),
            "vsego_v_baze": int(v_baze or 0), "verdikt": verdikt,
            # Сколько показывает сам FB. Это НЕ объявления бренда: FB ищет со
            # стеммингом, и по «ProstaMen» его 3 300 — это польское слово «prosta».
            # Держим рядом, чтобы разрыв был объясним, а не выглядел недосбором.
            "fb_schetchik": int(fb_schet) if fb_schet is not None else None,
            "fb_schetchik_at": str(fb_schet_at) if fb_schet_at else None,
        })
        itogo["konfigov"] += 1
        itogo["srezov"] += srezov
        itogo["kartochek"] += int(kart)
        itogo["novyh"] += int(novyh)

    return {
        "run_id": run_id, "status": status, "mode": mode,
        "nachat": str(ot), "zakonchen": str(do_),
        "srezov_v_plane": _v_plane or None,
        "srezov_sdelano": _sdelano or None,
        "vypolneno_procentov": _procent,
        "itogo": itogo,
        # Подозрительные — наверх: это единственное, что требует внимания человека.
        "konfigi": sorted(konfigi, key=lambda k: (k["verdikt"] != "подозрительный ноль",
                                                  -k["kartochek"])),
    }


@router.get("/logs", response_model=dict)
async def parser_logs(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    try:
        run = (await session.execute(
            select(ParserRun)
            .where(ParserRun.log_tail.is_not(None))
            .order_by(ParserRun.triggered_at.desc())
            .limit(1)
        )).scalar_one_or_none()
    except Exception:
        return {"log_tail": ""}

    return {"log_tail": run.log_tail if run else ""}


_LIVE_CACHE: dict = {"at": 0.0, "data": None}


@router.get("/live")
async def parser_live(
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    """Живая картина сбора — по данным БД, а не по «ранам».

    Старые /status и /logs показывают ParserRun, а сбор часто идёт прямыми джобами мимо
    ранов, поэтому там висят цифры позапрошлого прогона. Здесь считаем по факту:
    сколько залито за час и за сутки, разбивку по гео и прогресс по целевым дням.

    Все запросы опираются на индексы (first_seen_at, country, started_at) и уложены
    в ~2 секунды; ответ кешируется на 30 секунд, т.к. страница опрашивает его часто.
    """
    import time as _t
    now = _t.time()
    if _LIVE_CACHE["data"] is not None and now - _LIVE_CACHE["at"] < 30:
        return _LIVE_CACHE["data"]

    async def _scalar(sql: str):
        return (await session.execute(text(sql))).scalar() or 0

    last_hour = await _scalar(
        "SELECT count(*) FROM ads WHERE first_seen_at > now() - interval '1 hour'")
    last_24h = await _scalar(
        "SELECT count(*) FROM ads WHERE first_seen_at > now() - interval '24 hours'")

    by_geo = [
        {"country": r[0], "za_sutki": r[1]}
        for r in (await session.execute(text(
            "SELECT country, count(*) FROM ads "
            "WHERE first_seen_at > now() - interval '24 hours' "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 15"))).all()
    ]

    # Последний прогон — от него отсчитываем «сколько собрано именно сейчас».
    run_row = (await session.execute(text(
        "SELECT id, status, mode, coalesce(started_at, triggered_at) "
        "FROM parser_runs ORDER BY triggered_at DESC LIMIT 1"))).first()
    progon = None
    run_start = None
    if run_row:
        run_start = run_row[3]
        progon = {"id": run_row[0], "status": run_row[1], "mode": run_row[2],
                  "nachat": str(run_row[3])}

    # Целевой день = date_to минус сутки. Показываем ДВЕ цифры, и это принципиально:
    #   vsego_v_baze   — сколько всего есть по этому гео и дню за всё время;
    #   za_etot_progon — сколько добавилось с момента старта последнего прогона.
    # Без второй свежесозданный конфиг показывает чужой результат: неймспейс у гео
    # один, и объявления прошлых прогонов выглядят как «уже собрано этим конфигом».
    targets = []
    rows = (await session.execute(text(
        "SELECT c.id, c.country, c.date_to AS den, c.max_collect, (c.date_to + 1) "
        "FROM parsing_configs c "
        "WHERE c.is_active AND c.sort_mode = 'relevancy_monthly_grouped' "
        "  AND c.date_to IS NOT NULL ORDER BY c.id"))).all()
    for cid, country, den, full, date_to in rows:
        vsego = (await session.execute(text(
            "SELECT count(*) FROM ads WHERE country = :c "
            "  AND started_at >= :ot AND started_at < :do"),
            {"c": country, "ot": den, "do": date_to})).scalar() or 0
        za_progon = 0
        if run_start is not None:
            za_progon = (await session.execute(text(
                "SELECT count(*) FROM ads WHERE country = :c "
                "  AND started_at >= :ot AND started_at < :do "
                "  AND first_seen_at >= :s"),
                {"c": country, "ot": den, "do": date_to, "s": run_start})).scalar() or 0
        targets.append({
            "config_id": cid, "country": country, "day": str(den),
            "vsego_v_baze": vsego, "za_etot_progon": za_progon,
            "full": bool(full),
        })

    data = {
        "progon": progon,
        "zalito_za_chas": last_hour,
        "zalito_za_sutki": last_24h,
        "po_geo": by_geo,
        "celi": targets,
    }
    _LIVE_CACHE["at"] = now
    _LIVE_CACHE["data"] = data
    return data


class StartDayIn(BaseModel):
    """Запуск сбора из админки без знания внутренней механики.

    countries — список ISO-кодов (['BR','MX']); пустой = все активные гео-конфиги.
    day       — целевой день ('2026-06-01'); null = обычный сбор за весь период.
    depth     — 'quick' (базовая сетка, ~15 мин) или 'full' (плюс перебор по словам).
    """
    countries: list[str] = []
    day: date | None = None
    depth: str = "quick"


@router.post("/start-day")
async def start_day(
    payload: StartDayIn,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    if payload.depth not in ("quick", "full"):
        raise HTTPException(status_code=422, detail="depth: quick или full")

    active = await _active_run(session)
    if active:
        raise HTTPException(status_code=409, detail="Парсер уже запущен — сначала остановите текущий прогон")

    codes = [c.strip().upper() for c in payload.countries if c and c.strip()]
    if len(codes) > 50:
        raise HTTPException(status_code=422, detail="Слишком много стран за раз (максимум 50)")

    # Сбор «за день» = отсечка на СУТКИ ПОЗЖЕ целевого дня + ранжирование по свежести:
    # FB отдаёт самое новое под отсечкой, поэтому ~95% выдачи попадает на нужный день.
    # Без дня — обычный широкий сбор за весь период.
    if payload.day:
        sort_mode = "relevancy_monthly_grouped"
        # Храним САМ целевой день; отсечку (день + 1) считает координатор.
        date_to = payload.day  # celevoy den
    else:
        sort_mode = "total_impressions"
        date_to = date.today()

    tronuto = []
    tronuto_ids = []
    for code in codes:
        cfg = (await session.execute(
            select(ParsingConfig).where(
                ParsingConfig.country == code,
                ParsingConfig.sort_mode == sort_mode,
                ParsingConfig.config_type == "filters",
            ).limit(1)
        )).scalar_one_or_none()
        if cfg is None:
            cfg = ParsingConfig(
                country=code, config_type="filters", vertical="nutra",
                sort_mode=sort_mode, sort_direction="desc", active_status="all",
                date_from=date(2019, 1, 1),
                notes="создан из админки: сбор за день",
            )
            session.add(cfg)
        cfg.date_to = date_to
        cfg.is_active = True
        cfg.max_collect = (payload.depth == "full")
        tronuto.append(code)
        tronuto_ids.append(cfg.id)

    if codes:
        await session.commit()
        # id появляются только после commit — собираем их заново для адресного прогона.
        tronuto_ids = [
            c.id for c in (await session.execute(
                select(ParsingConfig).where(
                    ParsingConfig.country.in_(codes),
                    ParsingConfig.sort_mode == sort_mode,
                    ParsingConfig.config_type == "filters",
                )
            )).scalars().all()
        ]

    # Если страны указаны — прогон адресный: координатор возьмёт только эти конфиги.
    # Пусто — обычный прогон по всем активным.
    run = ParserRun(
        status="triggered", mode="filters",
        only_configs=",".join(str(i) for i in tronuto_ids) if tronuto_ids else None,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    return {
        "run_id": run.id,
        "strany": tronuto or "все активные",
        "celevoy_den": str(payload.day) if payload.day else "весь период",
        "otsechka": str(date_to + timedelta(days=1)) if payload.day else str(date_to),
        "glubina": payload.depth,
        "adresno": bool(tronuto_ids),
    }


_WORDS_CACHE: dict = {"at": 0.0, "data": None}


@router.get("/words")
async def parser_words(
    days: int = 7,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    """Статистика по поисковым словам и языкам.

    Раньше её было неоткуда взять: в объявление писался ключ конфига (у filters-конфигов
    он пуст), а результаты задач живут час. Теперь в строку пишется слово среза, поэтому
    видно, какое слово что нашло и на каких языках.
    """
    import time as _t
    now = _t.time()
    if _WORDS_CACHE["data"] is not None and now - _WORDS_CACHE["at"] < 120:
        return _WORDS_CACHE["data"]

    period = f"now() - interval '{max(1, min(days, 90))} days'"

    top = [
        {"slovo": r[0], "kreo": r[1], "yazykov": r[2]}
        for r in (await session.execute(text(
            f"SELECT keyword, count(*), count(DISTINCT language) FROM ads "
            f"WHERE keyword IS NOT NULL AND first_seen_at > {period} "
            f"GROUP BY 1 ORDER BY 2 DESC LIMIT 50"))).all()
    ]
    hudshie = [
        {"slovo": r[0], "kreo": r[1]}
        for r in (await session.execute(text(
            f"SELECT keyword, count(*) FROM ads "
            f"WHERE keyword IS NOT NULL AND first_seen_at > {period} "
            f"GROUP BY 1 ORDER BY 2 ASC LIMIT 30"))).all()
    ]
    po_yazykam = [
        {"yazyk": r[0] or "не определён", "kreo": r[1], "slov": r[2]}
        for r in (await session.execute(text(
            f"SELECT language, count(*), count(DISTINCT keyword) FROM ads "
            f"WHERE keyword IS NOT NULL AND first_seen_at > {period} "
            f"GROUP BY 1 ORDER BY 2 DESC LIMIT 25"))).all()
    ]

    data = {"za_dney": days, "top_slova": top, "slabye_slova": hudshie,
            "po_yazykam": po_yazykam}
    _WORDS_CACHE["at"] = now
    _WORDS_CACHE["data"] = data
    return data


@router.get("/slices")
async def parser_slices(
    limit: int = 40,
    session: AsyncSession = Depends(get_session),
    _=Depends(get_current_admin),
):
    """Живой лог срезов: что перебиралось последним и с каким выхлопом.

    Очередь RQ из API не видна (в контейнере нет модуля redis), поэтому воркер сам
    пишет короткую запись о каждом закрытом срезе в slice_events.
    """
    rows = (await session.execute(text(
        "SELECT finished_at, country, den, slovo, media, status, novyh, vsego, sekund "
        "FROM slice_events ORDER BY finished_at DESC LIMIT :lim"),
        {"lim": max(1, min(limit, 200))})).all()
    return [
        {"kogda": r[0].isoformat(), "geo": r[1], "den": str(r[2]) if r[2] else None,
         "slovo": r[3], "media": r[4], "status": r[5],
         "novyh": r[6], "vsego": r[7], "sekund": r[8]}
        for r in rows
    ]
