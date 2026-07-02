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
    try:
        al_main = (data.get("data") or {}).get("ad_library_main") or {}
        ad_cards = al_main.get("ad_cards") or {}
        edges = ad_cards.get("edges") or []
        nodes = [e.get("node", e) for e in edges]
        pi = ad_cards.get("pageInfo") or ad_cards.get("page_info") or {}
        cursor = pi.get("endCursor") or pi.get("end_cursor")
        has_next = bool(pi.get("hasNextPage") or pi.get("has_next_page"))
        return nodes, cursor, has_next
    except Exception as exc:
        logger.warning(f"[graphql] response parse error: {exc}")
        return [], None, False


def map_graphql_card(node: dict) -> ParsedCard:
    card = ParsedCard()
    card.library_id = str(node.get("id") or "")
    card.page_name = node.get("page_name")
    card.page_url = node.get("page_profile_uri")

    bodies = node.get("ad_creative_bodies") or []
    card.body_text = bodies[0] if bodies else None

    titles = node.get("ad_creative_link_titles") or []
    card.title = titles[0] if titles else None

    captions = node.get("ad_creative_link_captions") or []
    card.caption = captions[0] if captions else None

    snap = node.get("snapshot") or {}

    videos = snap.get("videos") or []
    card.video_urls = [
        v.get("video_hd_url") or v.get("video_sd_url")
        for v in videos
        if v.get("video_hd_url") or v.get("video_sd_url")
    ]
    card.poster_urls = [
        v.get("video_preview_image_url") or v.get("thumbnail_url")
        for v in videos
        if v.get("video_preview_image_url") or v.get("thumbnail_url")
    ]

    images = snap.get("images") or []
    card.image_urls = [
        img.get("original_image_url") or img.get("url")
        for img in images
        if img.get("original_image_url") or img.get("url")
    ]

    start_ts = node.get("start_date")
    if isinstance(start_ts, (int, float)):
        try:
            card.started_at = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        except Exception:
            pass

    card.is_active = bool(node.get("is_active"))
    card.cta_text = node.get("cta_type")
    card.platforms = list(node.get("publisher_platforms") or [])

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
