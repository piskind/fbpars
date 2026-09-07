import re
from typing import Optional, List
import os as _os
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_, and_, distinct, text, String, Integer, tuple_, cast, case, exists
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import get_current_client
from app.models_proxy import (
    Ad,
    Creative,
    ModerationEntry,
    ModerationStatus,
    ClientUser,
    ParsingConfig,
)
from app.schemas import AdOut


_FACETS_CACHE: dict = {"at": 0.0, "data": None}

router = APIRouter(prefix="/api/feed", tags=["feed"])


def _normalize_domain(d: str | None) -> str | None:
    """Чистый host: без схемы, www, пути, query и порта, в нижнем регистре.

    Принимает и голый домен ("ARTRONOL.ART"), и полный URL
    ("https://www.artronol.art/promo?x=1") — на выходе "artronol.art".
    """
    if not d:
        return None
    d = d.strip().lower()
    if "://" in d:
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0].split("?", 1)[0]  # отбрасываем путь/query
    if d.startswith("www."):
        d = d[4:]
    d = d.split(":", 1)[0]  # отбрасываем порт
    return d or None


# Реальная длительность открута: от старта (started_at, иначе first_seen_at) до
# последней активности (last_seen_at). Парсер писал в days_active «дней с последнего
# парса» → объявления 2021 года показывали 1. Считаем честно и в ответе, и в сортировке.
_DAYS_ACTIVE_EXPR = func.greatest(
    1,
    func.floor(
        func.extract(
            "epoch",
            Ad.last_seen_at - func.coalesce(Ad.started_at, Ad.first_seen_at),
        )
        / 86400
    ),
)


def _dedup_ci(values) -> list[str]:
    """Дедуп по регистру, сохраняя первое (канонич.) написание. "Apply Now"/"Apply now" → один."""
    seen: dict[str, str] = {}
    for v in values:
        if v is None:
            continue
        k = v.strip().lower()
        if k and k not in seen:
            seen[k] = v.strip()
    return sorted(seen.values(), key=str.lower)


def _real_days_active(ad) -> int:
    start = ad.started_at or ad.first_seen_at
    end = ad.last_seen_at or ad.first_seen_at
    if not start or not end:
        return max(1, ad.days_active or 1)
    return max(1, (end - start).days)


def _is_broad_filter_ad():
    """Объявление пришло из широкого парсинга «По фильтрам» (config_type='filters').

    Такие парсы тянут всё подряд по стране (игры/аппы/unicef и т.п.) и в конфиге
    у них проставлена вертикаль (напр. Нутра) — но по факту это сборная солянка.
    Считаем их «Без категории»: не относим к конкретной вертикали (Nutra и пр.).
    Матч по (keyword, country) через EXISTS — NULL-safe.
    """
    # keyword у широких фильтр-конфигов = NULL, и у их объявлений keyword тоже NULL.
    # Обычное "=" при NULL=NULL даёт NULL (не TRUE) → раньше правило ловило 0.
    # IS NOT DISTINCT FROM — NULL-safe сравнение.
    # Плоские подзапросы вместо коррелированного EXISTS: тот выполнялся для
    # КАЖДОЙ строки и один держал дашборд на 95 секундах.
    _bez_klyucha = (
        select(ParsingConfig.country)
        .where(ParsingConfig.config_type == "filters", ParsingConfig.keyword.is_(None))
        .scalar_subquery()
    )
    _s_klyuchom = (
        select(ParsingConfig.country, ParsingConfig.keyword)
        .where(ParsingConfig.config_type == "filters", ParsingConfig.keyword.is_not(None))
        .subquery()
    )
    return or_(
        and_(Ad.keyword.is_(None), Ad.country.in_(_bez_klyucha)),
        tuple_(Ad.country, Ad.keyword).in_(select(_s_klyuchom.c.country, _s_klyuchom.c.keyword)),
    )


# Со скольки знаков искать по началу слова. Ниже этого ищем слово целиком:
# «a:*» совпало бы с миллионами строк и повесило бы счётчик.
_MIN_DLINA_PREFIKSA = int(_os.getenv("MIN_DLINA_PREFIKSA", "3") or 3)

# Все фильтры /feed в одном месте — используются и списком, и счётчиком.
def _apply_ad_filters(
    stmt,
    *,
    country=None,
    countries=None,
    keyword=None,
    vertical=None,
    media_type=None,
    cta=None,
    platforms=None,
    domain=None,
    page_id=None,
    page_name=None,
    link_contains=None,
    language=None,
    lead_form=None,
    app_store=None,
    ecom_platform=None,
    ip=None,
    search=None,
    search_mode="exact",
    partner=None,
    country_count=None,
    is_active=None,
    days_active_min=None,
    days_active_max=None,
    started_from=None,
    started_to=None,
    last_seen_from=None,
    last_seen_to=None,
    reach_min=None,
    reach_max=None,
    spend_min=None,
    spend_max=None,
    gender=None,
    age_min=None,
    age_max=None,
    eu_country=None,
    used_in_ads_min=None,
    text_any=None,
    uncategorized=None,
):
    # ── Фильтр страны и "Кол-во стран" РАЗДЕЛЕНЫ, т.к. это про разные вещи ──
    # Простой фильтр "покажи объявления из MX" — по полю ads.country (страна парсинга,
    # ISO-код). Сравниваем в верхнем регистре, т.к. в БД встречается и "MX", и "mx".
    #   Раньше страну матчили по eu_countries (реальный охват), но там лежат НАЗВАНИЯ
    #   стран ("Mexico"/"Spain"), а не ISO-коды → "MX" не совпадал ни с чем и давал 0.
    # "Кол-во стран показа" (country_count ниже) — отдельно, по реальному охвату
    # (eu_countries). Так простой выбор страны всегда работает по country.
    _sel_countries = countries or ([country] if country else None)
    if _sel_countries:
        _sel_upper = [c.upper() for c in _sel_countries if c]
        # Все ячейки сбора слиты в ISO-коды (миграция 15.08), поэтому сравниваем
        # напрямую — так работает индекс ix_ads_country.
        stmt = stmt.where(func.upper(Ad.country).in_(_sel_upper))
    if keyword:
        # Без учёта регистра: ключи хранятся как их дал заказчик («NoLimit City»),
        # а в поиске набирают строчными. Индекс ix_ads_keyword_lower — под lower().
        stmt = stmt.where(func.lower(Ad.keyword) == keyword.strip().lower())
    if vertical:
        stmt = stmt.where(Ad.vertical == vertical)
        if vertical != "general":
            # в конкретную вертикаль (Nutra и пр.) объявления из широких фильтр-парсингов
            # не пускаем; для самой "Общее" (general) — наоборот, показываем их.
            stmt = stmt.where(~_is_broad_filter_ad())
    if uncategorized:
        # "Без категории" / "Общее": объявления из широких фильтр-парсингов,
        # либо без вертикали, либо помеченные vertical='general' (после backfill).
        stmt = stmt.where(or_(
            _is_broad_filter_ad(),
            Ad.vertical.is_(None),
            Ad.vertical == "general",
        ))
    if media_type:
        # мультивыбор формата (image/video/carousel/...)
        stmt = stmt.where(Ad.media_type.in_(media_type))
    if cta:
        # мультивыбор CTA, регистронезависимо, OR
        stmt = stmt.where(or_(*[Ad.cta_text.ilike(f"%{c}%") for c in cta]))
    if platforms:
        stmt = stmt.where(Ad.platforms.op("&&")(platforms))  # array overlap
    if text_any:
        # подкатегории/тематики: ключ есть в body ИЛИ page_name (регистронезависимо), OR
        stmt = stmt.where(or_(*[
            or_(Ad.body.ilike(f"%{kw}%"), Ad.page_name.ilike(f"%{kw}%"))
            for kw in text_any
        ]))
    if domain:
        # "Доменная зона": матч по ХОСТУ, а НЕ по подстроке URL.
        # Regex привязан к НАЧАЛУ строки: [схема://] [сабдомены.] <зона> <конец хоста>.
        #   ^([a-z]+://)?  — опциональная схема (для link_url; display_url без схемы);
        #   ([^/?#]*\.)?   — сабдомены до первого / ? # (в путь/query не выходим!);
        #   <зона> затем граница хоста ([/:?#] или конец).
        # Поэтому ".com" в query/пути ("...store/?u=x.com") НЕ ловится, а ".com" не
        # матчит ".store": у greenwellnessworld.store хост кончается на .store.
        # Юзер может ввести "com", ".com" или "example.com" — нормализуем.
        z = domain.strip().lstrip(".").lower()
        if z:
            pat = r"^([a-z]+://)?([^/?#]*\.)?" + re.escape(z) + r"([/:?#]|$)"
            stmt = stmt.where(or_(
                Ad.display_url.op("~*")(pat),
                Ad.link_url.op("~*")(pat),
            ))
    if page_id:
        stmt = stmt.where(Ad.page_id == page_id)
    if page_name:
        stmt = stmt.where(Ad.page_name.ilike(f"%{page_name}%"))
    if link_contains:
        stmt = stmt.where(Ad.link_url.ilike(f"%{link_contains}%"))
    if language:
        # мультивыбор языка, регистронезависимо
        stmt = stmt.where(func.lower(Ad.language).in_([l.lower() for l in language]))
    if lead_form is not None:
        stmt = stmt.where(Ad.lead_form.is_(lead_form))
    if app_store:
        stmt = stmt.where(Ad.app_store == app_store)
    if ecom_platform:
        stmt = stmt.where(Ad.ecom_platform == ecom_platform)
    if ip:
        stmt = stmt.where(Ad.ip == ip)
    if partner:
        # ads связываются с partner через parsing_configs (keyword, country)
        cfg_sub = (
            select(ParsingConfig.keyword, ParsingConfig.country)
            .where(ParsingConfig.partner.in_(partner))
        )
        stmt = stmt.where(tuple_(Ad.keyword, Ad.country).in_(cfg_sub))
    if country_count is not None:
        # Ровно N РЕАЛЬНЫХ стран показа — по охвату (eu_countries).
        # У не-EU объявлений разбивки по странам нет → считаем их моногео (1 страна).
        # Это независимо от фильтра страны выше (country vs охват — разные поля).
        stmt = stmt.where(
            func.coalesce(func.array_length(Ad.eu_countries, 1), 1) == country_count
        )
    if is_active is not None:
        stmt = stmt.where(Ad.is_active.is_(is_active))
    if days_active_min is not None:
        stmt = stmt.where(Ad.days_active >= days_active_min)
    if days_active_max is not None:
        stmt = stmt.where(Ad.days_active <= days_active_max)
    if started_from:
        stmt = stmt.where(Ad.started_at >= started_from)
    if started_to:
        # started_to приходит датой (2026-06-01) и парсится в полночь. Сравнение
        # started_at <= полночь выбрасывает весь этот день: FB отдаёт дату старта
        # как полночь по тихоокеанскому, т.е. 07:00 UTC. Трактуем верхнюю границу
        # как «весь указанный день включительно».
        if (started_to.hour, started_to.minute, started_to.second) == (0, 0, 0):
            stmt = stmt.where(Ad.started_at < started_to + timedelta(days=1))
        else:
            stmt = stmt.where(Ad.started_at <= started_to)
    if last_seen_from:
        stmt = stmt.where(Ad.last_seen_at >= last_seen_from)
    if last_seen_to:
        stmt = stmt.where(Ad.last_seen_at <= last_seen_to)
    if search:
        _zapros = search.strip()
        if _zapros.isdigit():
            # Одни цифры — это идентификатор объявления, ищем точно по нему.
            stmt = stmt.where(Ad.library_id == _zapros)
        else:
            # ВАЖНО: состав и порядок полей обязаны совпадать с индексом
            # ix_ads_poisk_fts, иначе он не будет использован.
            _tekst = (
                func.coalesce(Ad.body, "") + " " + func.coalesce(Ad.title, "")
                + " " + func.coalesce(Ad.caption, "") + " " + func.coalesce(Ad.page_name, "")
                + " " + func.coalesce(Ad.display_url, "")
            )
            _vektor = func.to_tsvector("simple", _tekst)

            # Разбираем запрос сами: to_tsquery требует готового выражения и падает
            # на любом спецсимволе, поэтому оставляем только буквы и цифры.
            _slova = [w for w in re.split(r"[^\w]+", _zapros, flags=re.UNICODE) if w]

            def _s_nachala(slovo: str) -> str:
                """Слово с меткой «искать по началу». Для огрызков в 1-2 знака
                метку не ставим: «a:*» совпало бы с миллионами строк."""
                return f"{slovo}:*" if len(slovo) >= _MIN_DLINA_PREFIKSA else slovo

            if not _slova:
                # В запросе не осталось ничего, кроме знаков препинания.
                stmt = stmt.where(text("false"))
            else:
                if search_mode == "broad":
                    # широкий: достаточно любого слова из запроса
                    _vyrazhenie = " | ".join(_s_nachala(w) for w in _slova)
                else:
                    # точный: слова подряд и в том же порядке
                    _vyrazhenie = " <-> ".join(_s_nachala(w) for w in _slova)
                stmt = stmt.where(_vektor.op("@@")(func.to_tsquery("simple", _vyrazhenie)))
    # Охват/Спенд — диапазоны. Данные есть ТОЛЬКО у EU-объявлений (reach/spend_estimate),
    # у не-EU они NULL → при этих фильтрах такие объявления не проходят (ожидаемо).
    if reach_min is not None:
        stmt = stmt.where(Ad.reach >= reach_min)
    if reach_max is not None:
        stmt = stmt.where(Ad.reach <= reach_max)
    if spend_min is not None:
        stmt = stmt.where(Ad.spend_estimate >= spend_min)
    if spend_max is not None:
        stmt = stmt.where(Ad.spend_estimate <= spend_max)
    if gender:
        # Пол таргетинга (EU): reach_breakdown.targeting.gender ("Men"/"Women"/"All").
        # best-effort ILIKE; "all" не фильтрует. Значение может быть локализовано.
        g = gender.lower()
        if g in ("men", "women"):
            stmt = stmt.where(
                Ad.reach_breakdown["targeting"]["gender"].astext.ilike(f"%{g}%")
            )
    if age_min is not None or age_max is not None:
        # Возраст таргетинга (EU): reach_breakdown.targeting.age вида "18 - 65+".
        # Берём нижнюю границу (первое число) — best-effort фильтр от/до.
        age_low = cast(
            func.substring(Ad.reach_breakdown["targeting"]["age"].astext, r"(\d+)"),
            Integer,
        )
        if age_min is not None:
            stmt = stmt.where(age_low >= age_min)
        if age_max is not None:
            stmt = stmt.where(age_low <= age_max)
    if eu_country:
        stmt = stmt.where(Ad.eu_countries.op("&&")(eu_country))
    if used_in_ads_min is not None:
        stmt = stmt.where(Ad.used_in_ads_count >= used_in_ads_min)
    return stmt


@router.get("", response_model=list[AdOut])
async def list_feed(
    country: str | None = Query(None),
    countries: list[str] | None = Query(None),
    keyword: str | None = Query(None),
    vertical: str | None = Query(None),
    media_type: list[str] | None = Query(None),
    cta: list[str] | None = Query(None),
    platforms: list[str] | None = Query(None),
    domain: str | None = Query(None),
    page_id: str | None = Query(None),
    page_name: str | None = Query(None),
    link_contains: str | None = Query(None),
    language: list[str] | None = Query(None),
    lead_form: bool | None = Query(None),
    app_store: str | None = Query(None),
    ecom_platform: str | None = Query(None),
    ip: str | None = Query(None),
    search: str | None = Query(None),
    search_mode: str = Query("exact", regex="^(exact|broad)$"),
    partner: list[str] | None = Query(None),
    country_count: int | None = Query(None),
    is_active: bool | None = Query(None),
    days_active_min: int | None = Query(None),
    days_active_max: int | None = Query(None),
    started_from: datetime | None = Query(None),
    started_to: datetime | None = Query(None),
    last_seen_from: datetime | None = Query(None),
    last_seen_to: datetime | None = Query(None),
    reach_min: int | None = Query(None),
    reach_max: int | None = Query(None),
    spend_min: int | None = Query(None),
    spend_max: int | None = Query(None),
    gender: str | None = Query(None),
    age_min: int | None = Query(None),
    age_max: int | None = Query(None),
    eu_country: list[str] | None = Query(None),
    used_in_ads_min: int | None = Query(None),
    text_any: list[str] | None = Query(None),
    uncategorized: bool | None = Query(None),
    sort: str = Query("newest", regex="^(newest|oldest|days_desc|days_asc)$"),
    pokazat_dubli: bool = Query(False),
    limit: int = Query(40, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    stmt = (
        select(Ad)
        .join(ModerationEntry, ModerationEntry.ad_id == Ad.id)
        .where(ModerationEntry.status == ModerationStatus.APPROVED)
        .options(selectinload(Ad.creatives))
    )
    # Повторы креатива скрыты по умолчанию: один баннер = одна карточка в ленте.
    # Флаг посчитан заранее, фильтр идёт по частичному индексу ix_ads_ne_dup.
    if not pokazat_dubli:
        stmt = stmt.where(Ad.is_dup.is_(False))

    stmt = _apply_ad_filters(
        stmt,
        country=country, countries=countries, keyword=keyword, vertical=vertical,
        media_type=media_type, cta=cta, platforms=platforms, domain=domain,
        page_id=page_id, page_name=page_name, link_contains=link_contains,
        language=language, lead_form=lead_form, app_store=app_store,
        ecom_platform=ecom_platform, ip=ip, search=search, search_mode=search_mode,
        partner=partner, country_count=country_count, is_active=is_active,
        days_active_min=days_active_min, days_active_max=days_active_max,
        started_from=started_from, started_to=started_to,
        last_seen_from=last_seen_from, last_seen_to=last_seen_to,
        reach_min=reach_min, reach_max=reach_max, spend_min=spend_min,
        spend_max=spend_max, gender=gender, age_min=age_min, age_max=age_max,
        eu_country=eu_country, used_in_ads_min=used_in_ads_min, text_any=text_any,
        uncategorized=uncategorized,
    )

    if sort == "newest":
        stmt = stmt.order_by(Ad.first_seen_at.desc())
    elif sort == "oldest":
        stmt = stmt.order_by(Ad.first_seen_at.asc())
    elif sort == "days_desc":
        stmt = stmt.order_by(Ad.days_active.desc())
    elif sort == "days_asc":
        stmt = stmt.order_by(Ad.days_active.asc())

    # Дедупликация по phash на уровне SQL.
    # 1) Берём список "репрезентативных" ad_id: для каждого phash оставляем
    #    самую свежую ad, ads без phash — все остаются как есть.
    from sqlalchemy import literal, case, func as sa_func

    base_subq = stmt.subquery()

    # phash первого creative для каждой ad
    first_phash_subq = (
        select(
            Creative.ad_id,
            sa_func.min(Creative.phash).label("phash"),
        )
        .where(Creative.phash.is_not(None))
        .group_by(Creative.ad_id)
        .subquery()
    )

    ad_with_phash = (
        select(
            base_subq.c.id.label("ad_id"),
            base_subq.c.first_seen_at,
            base_subq.c.days_active,
            first_phash_subq.c.phash,
        )
        .select_from(
            base_subq.outerjoin(
                first_phash_subq, base_subq.c.id == first_phash_subq.c.ad_id
            )
        )
        .subquery()
    )

    # Для каждого phash оставляем самую свежую ad (DISTINCT ON по phash).
    # ads без phash — попадают все (NULL уникален в DISTINCT ON).
    representatives_subq = (
        select(ad_with_phash.c.ad_id)
        .distinct(
            sa_func.coalesce(
                ad_with_phash.c.phash, sa_func.cast(ad_with_phash.c.ad_id, String)
            )
        )
        .order_by(
            sa_func.coalesce(
                ad_with_phash.c.phash, sa_func.cast(ad_with_phash.c.ad_id, String)
            ),
            ad_with_phash.c.first_seen_at.desc(),
        )
        .subquery()
    )

    # Считаем дубли для каждого репрезентанта.
    dupe_count_subq = (
        select(
            ad_with_phash.c.phash,
            (sa_func.count() - 1).label("dupes"),
        )
        .where(ad_with_phash.c.phash.is_not(None))
        .group_by(ad_with_phash.c.phash)
        .subquery()
    )

    # Дедуп по phash имеет смысл, только если phash где-то заполнен. Таблица creatives
    # сейчас пуста → coalesce(phash, ad_id) уникален для каждой строки, DISTINCT ON не
    # схлопывает НИЧЕГО, но заставляет полностью сортировать выборку на каждый запрос.
    # На 7 млн строк это и есть основной тормоз ленты. Пропускаем, пока phash не появится.
    _has_phash = (await session.execute(
        select(Creative.id).where(Creative.phash.is_not(None)).limit(1)
    )).first() is not None
    # Финальная выборка с пагинацией.
    if _has_phash:
        final_stmt = (
            select(Ad)
            .join(representatives_subq, Ad.id == representatives_subq.c.ad_id)
            .options(selectinload(Ad.creatives))
        )
    else:
        # Схлопывание ПОВТОРОВ КРЕАТИВА (один баннер, запущенный десятками
        # отдельных объявлений) пробовал делать здесь через DISTINCT ON по
        # отпечатку (страница+текст+заголовок). Замер: 16 с с фильтром по стране
        # и таймаут без фильтра — откачено. Правильный путь: хранить отпечаток
        # отдельной колонкой с индексом, а не считать md5 на лету по всей выборке.
        # Пока повторы отсекаются на СБОРЕ (repository._est_takoy_zhe_kreativ).
        final_stmt = stmt

    # При поиске отбираем совпадения заранее, иначе планировщик идёт по индексу
    # сортировки и перечитывает миллионы строк ради сорока подходящих.
    if search and not _has_phash:
        _sovpavshie = (
            stmt.with_only_columns(
                Ad.id.label("id"),
                Ad.first_seen_at.label("uvideno"),
                _DAYS_ACTIVE_EXPR.label("dney"),
            )
            .order_by(None)
            .cte("sovpavshie")
            .prefix_with("MATERIALIZED")
        )
        final_stmt = (
            select(Ad)
            .join(_sovpavshie, Ad.id == _sovpavshie.c.id)
            .options(selectinload(Ad.creatives))
        )
        _po = {
            "newest": _sovpavshie.c.uvideno.desc(),
            "oldest": _sovpavshie.c.uvideno.asc(),
            "days_desc": _sovpavshie.c.dney.desc(),
            "days_asc": _sovpavshie.c.dney.asc(),
        }.get(sort)
        if _po is not None:
            final_stmt = final_stmt.order_by(_po)
    elif sort == "newest":
        final_stmt = final_stmt.order_by(Ad.first_seen_at.desc())
    elif sort == "oldest":
        final_stmt = final_stmt.order_by(Ad.first_seen_at.asc())
    elif sort == "days_desc":
        final_stmt = final_stmt.order_by(_DAYS_ACTIVE_EXPR.desc())
    elif sort == "days_asc":
        final_stmt = final_stmt.order_by(_DAYS_ACTIVE_EXPR.asc())

    final_stmt = final_stmt.limit(limit).offset(offset)
    items = list((await session.execute(final_stmt)).scalars().all())

    # Подтягиваем duplicates_count
    phashes_in_page = []
    for ad in items:
        ph = next((c.phash for c in ad.creatives if c.phash), None)
        if ph:
            phashes_in_page.append(ph)

    dupe_map: dict[str, int] = {}
    if phashes_in_page:
        rows = (await session.execute(
            select(Creative.phash, sa_func.count(Creative.id))
            .where(Creative.phash.in_(phashes_in_page))
            .group_by(Creative.phash)
        )).all()
        dupe_map = {ph: cnt - 1 for ph, cnt in rows}

    for ad in items:
        ph = next((c.phash for c in ad.creatives if c.phash), None)
        ad.duplicates_count = dupe_map.get(ph, 0) if ph else 0

    # Partner lookup via parsing_configs (keyword + country)
    _keywords = list({ad.keyword for ad in items if ad.keyword})
    _partner_map: dict[tuple, str | None] = {}
    _cfgtype_map: dict[tuple, str | None] = {}
    if _keywords:
        _cfg_rows = (await session.execute(
            select(
                ParsingConfig.keyword, ParsingConfig.country,
                ParsingConfig.partner, ParsingConfig.config_type,
            )
            .where(ParsingConfig.keyword.in_(_keywords))
        )).all()
        _partner_map = {(r.keyword, r.country): r.partner for r in _cfg_rows}
        _cfgtype_map = {(r.keyword, r.country): r.config_type for r in _cfg_rows}
    for ad in items:
        ad.partner = _partner_map.get((ad.keyword, ad.country)) if ad.keyword else None
        ad.days_active = _real_days_active(ad)
        # объявления из широких фильтр-парсингов показываем как "Без категории"
        if _cfgtype_map.get((ad.keyword, ad.country)) == "filters":
            ad.vertical = None

    return items


# Счётчик без фильтров для главной: держим в памяти, пересчитываем по таймеру.
_TOTAL_TTL = int(_os.getenv("FEED_TOTAL_TTL", "300") or 300)
_TOTAL_KESH: dict = {"znachenie": None, "do": 0.0}

# Имена параметров, которые НЕ являются фильтрами (пагинация, сессия, служебное).
_NE_FILTRY = {"session", "_", "search_mode", "sort", "limit", "offset"}


def _bez_filtrov(mestnye: dict) -> bool:
    """True, если запрос пришёл без единого фильтра — то есть это главная страница."""
    for imya, znach in mestnye.items():
        if imya in _NE_FILTRY or imya.startswith("_"):
            continue
        if imya == "pokazat_dubli":
            # false — это умолчание, а не фильтр; true меняет выборку.
            if not znach:
                continue
            return False
        if znach is None or znach == [] or znach == "":
            continue
        return False
    return True


# Счётчик с фильтрами: США — 2.09 млн строк и 14 секунд честного пересчёта.
# Держим по ячейке на набор фильтров, не больше _KESH_FILTROV_MAX штук.
_KESH_FILTROV: dict = {}
_KESH_FILTROV_MAX = 100


def _klyuch_filtrov(mestnye: dict) -> tuple:
    """Устойчивый ключ кеша из параметров запроса."""
    chasti = []
    for imya in sorted(mestnye):
        if imya in _NE_FILTRY or imya.startswith("_"):
            continue
        znach = mestnye[imya]
        if imya == "pokazat_dubli" and not znach:
            continue
        if znach is None or znach == [] or znach == "":
            continue
        chasti.append((imya, tuple(znach) if isinstance(znach, list) else znach))
    return tuple(chasti)


@router.get("/count")
async def feed_count(
    country: str | None = Query(None),
    countries: list[str] | None = Query(None),
    keyword: str | None = Query(None),
    vertical: str | None = Query(None),
    media_type: list[str] | None = Query(None),
    cta: list[str] | None = Query(None),
    platforms: list[str] | None = Query(None),
    domain: str | None = Query(None),
    page_id: str | None = Query(None),
    page_name: str | None = Query(None),
    link_contains: str | None = Query(None),
    language: list[str] | None = Query(None),
    lead_form: bool | None = Query(None),
    app_store: str | None = Query(None),
    ecom_platform: str | None = Query(None),
    ip: str | None = Query(None),
    search: str | None = Query(None),
    search_mode: str = Query("exact", regex="^(exact|broad)$"),
    partner: list[str] | None = Query(None),
    country_count: int | None = Query(None),
    is_active: bool | None = Query(None),
    days_active_min: int | None = Query(None),
    days_active_max: int | None = Query(None),
    started_from: datetime | None = Query(None),
    started_to: datetime | None = Query(None),
    last_seen_from: datetime | None = Query(None),
    last_seen_to: datetime | None = Query(None),
    reach_min: int | None = Query(None),
    reach_max: int | None = Query(None),
    spend_min: int | None = Query(None),
    spend_max: int | None = Query(None),
    gender: str | None = Query(None),
    age_min: int | None = Query(None),
    age_max: int | None = Query(None),
    eu_country: list[str] | None = Query(None),
    used_in_ads_min: int | None = Query(None),
    text_any: list[str] | None = Query(None),
    uncategorized: bool | None = Query(None),
    pokazat_dubli: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    """Кол-во объявлений после фильтров (повторы креатива скрыты, как в списке)."""
    # Снимаем параметры ДО того, как появятся рабочие переменные: иначе проверка
    # «есть ли фильтры» спотыкается о них и всегда отвечает «есть».
    _parametry = dict(locals())
    base = (
        select(Ad.id)
        .join(ModerationEntry, ModerationEntry.ad_id == Ad.id)
        .where(ModerationEntry.status == ModerationStatus.APPROVED)
    )
    base = _apply_ad_filters(
        base,
        country=country, countries=countries, keyword=keyword, vertical=vertical,
        media_type=media_type, cta=cta, platforms=platforms, domain=domain,
        page_id=page_id, page_name=page_name, link_contains=link_contains,
        language=language, lead_form=lead_form, app_store=app_store,
        ecom_platform=ecom_platform, ip=ip, search=search, search_mode=search_mode,
        partner=partner, country_count=country_count, is_active=is_active,
        days_active_min=days_active_min, days_active_max=days_active_max,
        started_from=started_from, started_to=started_to,
        last_seen_from=last_seen_from, last_seen_to=last_seen_to,
        reach_min=reach_min, reach_max=reach_max, spend_min=spend_min,
        spend_max=spend_max, gender=gender, age_min=age_min, age_max=age_max,
        eu_country=eu_country, used_in_ads_min=used_in_ads_min, text_any=text_any,
        uncategorized=uncategorized,
    )
    # Повторы креатива скрыты так же, как в списке ленты, иначе счётчик и список
    # показывали бы разные числа.
    if not pokazat_dubli:
        base = base.where(Ad.is_dup.is_(False))
    filtered = base.subquery()

    # Дедуп по phash не делаем: таблица creatives пуста (медиа не скачиваем, отдаём
    # прямые ссылки FB CDN), distinct ничего не схлопывал, но заставлял сортировать
    # 7.4 млн строк с выгрузкой на диск — счётчик не успевал ответить, и фронт
    # показывал размер страницы вместо общего числа.
    count_stmt = select(func.count()).select_from(filtered)

    # Без фильтров это «сколько всего у нас объявлений» — цифра для главной. Честный
    # пересчёт join'а на 5.9 млн строк занимает 14 секунд, фронт столько не ждёт и
    # рисует размер страницы. Держим её в памяти и обновляем раз в FEED_TOTAL_TTL.
    import time as _time
    _tek = _time.time()
    _klyuch = _klyuch_filtrov(_parametry)

    # Пустой запрос (главная) держим в отдельной ячейке: её греет старт приложения.
    if _bez_filtrov(_parametry):
        if _TOTAL_KESH["do"] > _tek and _TOTAL_KESH["znachenie"] is not None:
            return {"total": _TOTAL_KESH["znachenie"]}
        total = (await session.execute(count_stmt)).scalar_one()
        _TOTAL_KESH["znachenie"] = total
        _TOTAL_KESH["do"] = _tek + _TOTAL_TTL
        return {"total": total}

    _yacheyka = _KESH_FILTROV.get(_klyuch)
    if _yacheyka and _yacheyka[0] > _tek:
        return {"total": _yacheyka[1]}

    total = (await session.execute(count_stmt)).scalar_one()
    if len(_KESH_FILTROV) >= _KESH_FILTROV_MAX:
        _KESH_FILTROV.pop(next(iter(_KESH_FILTROV)), None)
    _KESH_FILTROV[_klyuch] = (_tek + _TOTAL_TTL, total)
    return {"total": total}


@router.get("/partners")
async def feed_partners(
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    rows = (await session.execute(
        select(distinct(ParsingConfig.partner))
        .where(ParsingConfig.partner.is_not(None), ParsingConfig.partner != "")
        .order_by(ParsingConfig.partner)
    )).scalars().all()
    return {"partners": list(rows)}


# Разбивка по вертикалям: полный проход по одобренным, держим в памяти.
_VERT_KESH: dict = {"do": 0.0, "dannye": None}


@router.get("/vertical-counts")
async def vertical_counts(
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    # Считаем ТАК ЖЕ, как верхний счётчик /feed/count: обычный count и скрытые
    # повторы, иначе число у вертикали расходится с верхним.
    import time as _time
    _tek = _time.time()
    if _VERT_KESH["do"] > _tek and _VERT_KESH["dannye"] is not None:
        return {"counts": _VERT_KESH["dannye"]}

    base = (
        select(
            Ad.id,
            # широкие фильтр-парсинги / general → "Без категории" (unknown), не в вертикаль
            case(
                (or_(_is_broad_filter_ad(), Ad.vertical == "general"), None),
                else_=Ad.vertical,
            ).label("vertical"),
        )
        .join(ModerationEntry, ModerationEntry.ad_id == Ad.id)
        .where(ModerationEntry.status == ModerationStatus.APPROVED,
               Ad.is_dup.is_(False))
        .subquery()
    )
    rows = (await session.execute(
        select(base.c.vertical, func.count())
        .select_from(base)
        .group_by(base.c.vertical)
    )).all()
    _schet = {(v or "unknown"): c for v, c in rows}
    _VERT_KESH["dannye"] = _schet
    _VERT_KESH["do"] = _tek + _TOTAL_TTL
    return {"counts": _schet}


@router.get("/facets")
async def facets(
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    """Значения для выпадашек фильтров.

    Раньше каждый список считался как DISTINCT по ads JOIN moderation_queue без
    ограничений: на 7.5 млн строк это ~14 секунд НА КАЖДЫЙ из восьми списков, и морда
    просто не дожидалась — фильтры выглядели пустыми. Теперь два изменения:
      1) «скачущий» обход по индексу (recursive CTE) — берёт только уникальные значения,
         не читая таблицу целиком; для колонок с индексом это миллисекунды;
      2) кеш на 5 минут — набор стран/языков меняется медленно.
    Join к модерации убран: запись в moderation_queue создаётся на каждое объявление,
    поэтому на состав списков он не влияет, а стоит полного прохода.
    """
    import time as _time
    now = _time.time()
    if _FACETS_CACHE["data"] is not None and now - _FACETS_CACHE["at"] < 300:
        return _FACETS_CACHE["data"]

    # «Скачущий» обход быстр ТОЛЬКО по колонке с индексом: без него каждый шаг —
    # полная сортировка 7 млн строк, и эндпоинт зависает целиком. Поэтому каждый
    # список считаем со своим лимитом времени и по отдельности: медленная колонка
    # отдаёт пустой список, а не роняет все фильтры разом.
    _facet_timeout = 4000  # мс

    async def _distinct(col: str) -> list:
        q = text(
            f"WITH RECURSIVE t AS ("
            f"  (SELECT {col} AS v FROM ads WHERE {col} IS NOT NULL ORDER BY {col} LIMIT 1)"
            f"  UNION ALL"
            f"  SELECT (SELECT {col} FROM ads WHERE {col} > t.v AND {col} IS NOT NULL"
            f"          ORDER BY {col} LIMIT 1) FROM t WHERE t.v IS NOT NULL"
            f") SELECT v FROM t WHERE v IS NOT NULL"
        )
        try:
            await session.execute(text(f"SET LOCAL statement_timeout = {_facet_timeout}"))
            rows = (await session.execute(q)).scalars().all()
            return [r for r in rows if r is not None]
        except Exception as exc:
            await session.rollback()
            logger.warning(f"[facets] список {col} пропущен: {type(exc).__name__}")
            return []

    countries = sorted({c.upper() for c in await _distinct("country") if c})
    keywords = await _distinct("keyword")
    verticals = await _distinct("vertical")
    media_types = await _distinct("media_type")
    ctas = await _distinct("cta_text")
    languages = await _distinct("language")
    app_stores = await _distinct("app_store")
    ecom_platforms = await _distinct("ecom_platform")

    # Тот же лимит времени: без обёртки этот запрос наследует statement_timeout
    # от предыдущих и роняет весь ответ пятисоткой.
    try:
        await session.execute(text(f"SET LOCAL statement_timeout = {_facet_timeout}"))
        # Значений тут всего несколько (fb/ig/audience_network/messenger), поэтому
        # разворачивать массивы по всем 7.5 млн строк незачем — берём выборку.
        platforms_rows = (await session.execute(
            text("SELECT DISTINCT unnest(platforms) AS p FROM ("
                 "  SELECT platforms FROM ads WHERE platforms IS NOT NULL LIMIT 50000"
                 ") s ORDER BY p")
        )).scalars().all()
    except Exception as exc:
        await session.rollback()
        logger.warning(f"[facets] список platforms пропущен: {type(exc).__name__}")
        platforms_rows = []

    data = {
        "countries": list(countries),
        "keywords": list(keywords),
        "verticals": list(verticals),
        "media_types": list(media_types),
        "ctas": _dedup_ci(ctas),
        "languages": _dedup_ci(languages),
        "app_stores": list(app_stores),
        "ecom_platforms": list(ecom_platforms),
        "platforms": list(platforms_rows),
    }
    _FACETS_CACHE["at"] = now
    _FACETS_CACHE["data"] = data
    return data


@router.get("/{ad_id}", response_model=AdOut)
async def get_ad(
    ad_id: int,
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    stmt = (
        select(Ad)
        .join(ModerationEntry, ModerationEntry.ad_id == Ad.id)
        .where(Ad.id == ad_id, ModerationEntry.status == ModerationStatus.APPROVED)
        .options(selectinload(Ad.creatives))
    )
    ad = (await session.execute(stmt)).scalar_one_or_none()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")

    phashes = {c.phash for c in ad.creatives if c.phash}
    max_dupes = 0
    if phashes:
        rows = (await session.execute(
            select(Creative.phash, func.count(Creative.id))
            .where(Creative.phash.in_(phashes))
            .group_by(Creative.phash)
        )).all()
        for _ph, cnt in rows:
            if cnt - 1 > max_dupes:
                max_dupes = cnt - 1
    ad.duplicates_count = max_dupes

    if ad.keyword:
        _cfg = (await session.execute(
            select(ParsingConfig.partner, ParsingConfig.config_type)
            .where(ParsingConfig.keyword == ad.keyword, ParsingConfig.country == ad.country)
        )).first()
        ad.partner = _cfg.partner if _cfg else None
        # объявления из широких фильтр-парсингов показываем как "Без категории"
        if _cfg and _cfg.config_type == "filters":
            ad.vertical = None
    else:
        ad.partner = None

    ad.days_active = _real_days_active(ad)
    return ad


@router.get("/{ad_id}/similar", response_model=list[AdOut])
async def similar_ads(
    ad_id: int,
    by: str = Query("fp", regex="^(fp|domain|ip)$"),
    limit: int = Query(12, le=50),
    session: AsyncSession = Depends(get_session),
    _: ClientUser = Depends(get_current_client),
):
    base = (await session.execute(
        select(Ad)
        .options(selectinload(Ad.creatives))
        .where(Ad.id == ad_id)
    )).scalar_one_or_none()
    if not base:
        raise HTTPException(status_code=404, detail="Ad not found")

    conditions = []

    if by == "fp":
        phashes = {c.phash for c in base.creatives if c.phash}
        if base.page_id:
            conditions.append(Ad.page_id == base.page_id)
        if phashes:
            sub = select(Creative.ad_id).where(Creative.phash.in_(phashes))
            conditions.append(Ad.id.in_(sub))
    elif by == "ip":
        if base.ip:
            conditions.append(Ad.ip == base.ip)
    else:  # by == "domain"
        # Чистый домен берём из display_url, а если пусто (напр. TLD .art, который
        # парсер не кладёт в display_url) — из link_url. Обе стороны нормализуем.
        nd = _normalize_domain(base.display_url) or _normalize_domain(base.link_url)
        if nd:
            conditions.append(or_(
                Ad.display_url.ilike(f"%{nd}%"),
                Ad.link_url.ilike(f"%{nd}%"),
            ))

    if not conditions:
        return []

    stmt = (
        select(Ad)
        .join(ModerationEntry, ModerationEntry.ad_id == Ad.id)
        .where(
            Ad.id != ad_id,
            ModerationEntry.status == ModerationStatus.APPROVED,
            # Повторы креатива не показываем нигде, похожие не исключение.
            Ad.is_dup.is_(False),
            or_(*conditions),
        )
        .options(selectinload(Ad.creatives))
        .order_by(Ad.first_seen_at.desc())
        .limit(limit)
    )
    items = list((await session.execute(stmt)).scalars().all())

    for ad in items:
        ad.duplicates_count = 0

    return items