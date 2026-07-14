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


# TEMP: log snapshot structure once per process to confirm field names on live
# data, then remove. See BUG task (parser-v2-scale).
_snapshot_logged = False


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

    # Texts, media, links live inside snapshot.
    snap = node.get("snapshot") or {}

    global _snapshot_logged
    if not _snapshot_logged and snap:
        logger.info(f"[graphql] snapshot keys (once): {sorted(snap.keys())}")
        cards0 = (snap.get("cards") or [None])[0]
        if isinstance(cards0, dict):
            logger.info(f"[graphql] snapshot.cards[0] keys: {sorted(cards0.keys())}")
        _snapshot_logged = True

    card.page_name = node.get("page_name") or snap.get("page_name") or snap.get("current_page_name")
    card.page_url = snap.get("page_profile_uri")

    card.body_text = _text(snap.get("body"))
    card.title = snap.get("title")
    card.caption = snap.get("caption")
    card.cta_text = snap.get("cta_text") or snap.get("cta_type")
    card.link_url = snap.get("link_url")

    image_urls: list[str] = []
    video_urls: list[str] = []
    poster_urls: list[str] = []

    for img in snap.get("images") or []:
        image_urls.append(img.get("original_image_url") or img.get("resized_image_url") or img.get("url"))
    for v in snap.get("videos") or []:
        video_urls.append(v.get("video_hd_url") or v.get("video_sd_url"))
        poster_urls.append(v.get("video_preview_image_url") or v.get("thumbnail_url"))

    # Carousel/DCO ads carry per-card media (and often the only texts) in cards[].
    cards = snap.get("cards") or []
    if cards:
        c0 = cards[0]
        card.body_text = card.body_text or _text(c0.get("body"))
        card.title = card.title or c0.get("title")
        card.caption = card.caption or c0.get("caption")
        card.cta_text = card.cta_text or c0.get("cta_text")
        card.link_url = card.link_url or c0.get("link_url")
    for c in cards:
        image_urls.append(c.get("original_image_url") or c.get("resized_image_url"))
        video_urls.append(c.get("video_hd_url") or c.get("video_sd_url"))
        poster_urls.append(c.get("video_preview_image_url"))

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
