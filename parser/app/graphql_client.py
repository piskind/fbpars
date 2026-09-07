import json
from datetime import datetime, timezone

from loguru import logger

from app.parsers.library_card import ParsedCard


def _build_form_data(tokens: dict, variables: dict) -> dict:
    base = tokens.get("base_form_data")
    if base:
        # Use the exact form fields the browser sent — guarantees completeness
        data = {**base, "variables": json.dumps(variables, ensure_ascii=False)}
        data["fb_api_req_friendly_name"] = "AdLibrarySearchPaginationQuery"
        if "doc_id" in tokens:
            data["doc_id"] = tokens["doc_id"]
        return data

    # Fallback if browser form capture failed
    data = {
        "av": "0",
        "__aaid": "0",
        "__user": "0",
        "__a": "1",
        "lsd": tokens.get("lsd", ""),
        "jazoest": tokens.get("jazoest", ""),
        "__s": tokens.get("__s", ""),
        "__dyn": tokens.get("__dyn", ""),
        "__csr": tokens.get("__csr", ""),
        "__rev": tokens.get("__rev", ""),
        "__hsi": tokens.get("__hsi", ""),
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": "AdLibrarySearchPaginationQuery",
        "server_timestamps": "true",
        "variables": json.dumps(variables, ensure_ascii=False),
    }
    if "doc_id" in tokens:
        data["doc_id"] = tokens["doc_id"]
    return data


def _parse_response_json(text: str) -> dict:
    if text.startswith("for (;;);"):
        text = text[9:]
    return json.loads(text)


def _extract_ads_and_cursor(data: dict) -> tuple[list[dict], str | None, bool]:
    """Real FB Ad Library shape:
        data.ad_library_main.search_results_connection
            .edges[].node.collated_results[]   <- ad cards
            .page_info { end_cursor, has_next_page }
    Each collated_result is one ad card. We stamp `id` = ad_archive_id so the
    existing dedup in the paginators (which reads node["id"]) keeps working.
    """
    try:
        al_main = (data.get("data") or {}).get("ad_library_main") or {}
        src = al_main.get("search_results_connection") or {}
        edges = src.get("edges") or []
        nodes: list[dict] = []
        for e in edges:
            node = e.get("node") or {}
            for cr in node.get("collated_results") or []:
                if cr:
                    cr.setdefault("id", cr.get("ad_archive_id"))
                    nodes.append(cr)
        pi = src.get("page_info") or {}
        cursor = pi.get("end_cursor")
        has_next = bool(pi.get("has_next_page"))
        return nodes, cursor, has_next
    except Exception as exc:
        logger.warning(f"[graphql] response parse error: {exc}")
        return [], None, False


def _text(v) -> str | None:
    """snapshot text fields are sometimes {'text': ...}, sometimes plain str."""
    if isinstance(v, dict):
        v = v.get("text")
    if isinstance(v, str):
        return v or None
    return None


def _dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


_RE_SHABLON = _re_shablon = __import__("re").compile(r"\{\{[^{}]*\}\}")


def _ne_shablon(v):
    """Текст или None, если это нерендеренный шаблон вроде «{{product.description}}».

    У карусельных и динамических объявлений верхний snapshot.body содержит
    плейсхолдер, а настоящий текст лежит в snapshot.cards[].body. Плейсхолдер —
    непустая строка, поэтому подстановка через `or` не срабатывала никогда:
    в базу легло 1 053 222 объявления (14%) с «{{product.description}}» вместо
    текста, а отсев по ключу не видел в них бренда и выбрасывал живую рекламу
    (по «Adenofrin» в ES так терялась вся выдача точной фразы).
    """
    t = _text(v)
    if not t:
        return None
    # Убираем плейсхолдеры: если осмысленного текста не осталось — считаем пустым.
    ostatok = _RE_SHABLON.sub("", t).strip()
    return t if ostatok else None


def map_graphql_card(node: dict) -> ParsedCard:
    card = ParsedCard()
    card.library_id = str(node.get("ad_archive_id") or node.get("id") or "")
    card.is_active = bool(node.get("is_active"))
    card.platforms = list(node.get("publisher_platform") or node.get("publisher_platforms") or [])

    coll = node.get("collation_count")
    if isinstance(coll, int) and coll > 0:
        card.used_in_ads_count = coll

    start_ts = node.get("start_date")
    if isinstance(start_ts, (int, float)):
        try:
            card.started_at = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        except Exception:
            pass

    # end_date — дата окончания показа (0/None у ещё крутящихся).
    end_ts = node.get("end_date")
    if isinstance(end_ts, (int, float)) and end_ts > 0:
        try:
            card.ended_at = datetime.fromtimestamp(end_ts, tz=timezone.utc)
        except Exception:
            pass

    # Texts, media, links live inside snapshot.
    snap = node.get("snapshot") or {}

    card.page_name = node.get("page_name") or snap.get("page_name") or snap.get("current_page_name")
    card.page_url = snap.get("page_profile_uri")
    # id берём из ответа напрямую: у FB встречаются обе раскладки, плюс запасной
    # вариант — числовая ссылка (её разбирает repository._extract_page_id).
    _pid = node.get("page_id") or snap.get("page_id") or node.get("pageID")
    card.page_id = str(_pid) if _pid else None

    card.body_text = _ne_shablon(snap.get("body"))
    card.title = _ne_shablon(snap.get("title"))
    card.caption = _ne_shablon(snap.get("caption"))
    card.cta_text = snap.get("cta_text") or snap.get("cta_type")
    card.link_url = snap.get("link_url")

    # Carousel/DCO ads carry per-card media (and often the only texts) in cards[].
    cards = snap.get("cards") or []
    for _c in cards:
        # Идём по ВСЕМ карточкам: у карусели текст бывает не в первой, а плейсхолдер
        # стоит в нескольких подряд. Берём первое непустое и нешаблонное значение.
        card.body_text = card.body_text or _ne_shablon(_c.get("body"))
        card.title = card.title or _ne_shablon(_c.get("title"))
        card.caption = card.caption or _ne_shablon(_c.get("caption"))
        card.cta_text = card.cta_text or _c.get("cta_text")
        card.link_url = card.link_url or _c.get("link_url")
        if card.body_text and card.title and card.link_url:
            break

    # Media lives in cards[] on live data (snapshot.images/videos come back empty
    # even for ads that have media — even single image/video ads use cards[0]).
    # Walk every card; prefer original/HD over resized/SD.
    image_urls: list[str] = []
    video_urls: list[str] = []
    poster_urls: list[str] = []
    for c in cards:
        img = c.get("original_image_url") or c.get("resized_image_url")
        if img:
            image_urls.append(img)
        vid = c.get("video_hd_url") or c.get("video_sd_url")
        if vid:
            video_urls.append(vid)
            poster = c.get("video_preview_image_url")
            if poster:
                poster_urls.append(poster)

    # Fallback: some ad types may still populate snapshot.images/videos directly.
    if not image_urls and not video_urls:
        for img in snap.get("images") or []:
            u = img.get("original_image_url") or img.get("resized_image_url") or img.get("url")
            if u:
                image_urls.append(u)
        for v in snap.get("videos") or []:
            vu = v.get("video_hd_url") or v.get("video_sd_url")
            if vu:
                video_urls.append(vu)
            p = v.get("video_preview_image_url") or v.get("thumbnail_url")
            if p:
                poster_urls.append(p)

    card.image_urls = _dedup(image_urls)
    card.video_urls = _dedup(video_urls)
    card.poster_urls = _dedup(poster_urls)

    return card


if __name__ == "__main__":
    import asyncio as _asyncio

    async def _test():
        from app.browser import scrape_via_page_fetch

        test_url = (
            "https://www.facebook.com/ads/library/"
            "?country=PE&q=%E2%A0%80&active_status=all&ad_type=all&media_type=all"
            "&search_type=keyword_unordered"
        )
        print("Scraping via in-page fetch pagination...")
        raw_ads = await scrape_via_page_fetch(test_url, max_ads=20)

        print(f"\nTotal raw ads received: {len(raw_ads)}")
        for i, node in enumerate(raw_ads[:5]):
            card = map_graphql_card(node)
            print(f"\n--- Ad {i + 1} ---")
            print(f"  library_id : {card.library_id}")
            print(f"  page_name  : {card.page_name}")
            print(f"  is_active  : {card.is_active}")
            print(f"  started_at : {card.started_at}")
            print(f"  body_text  : {(card.body_text or '')[:120]!r}")
            print(f"  image_urls : {card.image_urls[:1]}")
            print(f"  video_urls : {card.video_urls[:1]}")

    _asyncio.run(_test())
