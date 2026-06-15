import asyncio
import sys
from loguru import logger
from app.browser import browser_context, build_library_url, goto_with_challenge_retry
from app.parsers.library_card import parse_card_text
from app.parsers.library_extractor import scroll_and_collect


async def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else "oxys"
    country = sys.argv[2] if len(sys.argv) > 2 else "PE"
    url = build_library_url(country, keyword)
    logger.info(f"Opening: {url}")

    async with browser_context() as context:
        page = await context.new_page()
        ok = await goto_with_challenge_retry(page, url)
        if not ok:
            logger.error("Failed to load page")
            return
        try:
            await page.wait_for_selector('div:has-text("Library ID")', timeout=30_000)
        except Exception:
            logger.warning("Timeout waiting for Library ID — trying anyway")
        await asyncio.sleep(5)

        raw_cards = await scroll_and_collect(page, max_scrolls=30)

        parsed_cards = []
        for raw in raw_cards:
            card = parse_card_text(raw["text"])
            card.image_urls = [img["src"] for img in raw["images"]]
            card.video_urls = [v["src"] for v in raw["videos"] if v["src"]]
            card.poster_urls = [v["poster"] for v in raw["videos"] if v["poster"]]
            card.page_url = raw["page_url"]
            card.link_url = raw["external_url"]
            parsed_cards.append(card)

        logger.info(f"=== TOTAL: {len(parsed_cards)} cards parsed ===")

        with_id = sum(1 for c in parsed_cards if c.library_id)
        with_page = sum(1 for c in parsed_cards if c.page_name)
        with_body = sum(1 for c in parsed_cards if c.body_text)
        with_cta = sum(1 for c in parsed_cards if c.cta_text)
        with_link = sum(1 for c in parsed_cards if c.link_url)
        with_page_url = sum(1 for c in parsed_cards if c.page_url)
        with_display = sum(1 for c in parsed_cards if c.display_url)
        with_image = sum(1 for c in parsed_cards if c.image_urls)
        with_video = sum(1 for c in parsed_cards if c.video_urls)

        logger.info(f"  library_id:  {with_id}")
        logger.info(f"  page_name:   {with_page}")
        logger.info(f"  page_url:    {with_page_url}")
        logger.info(f"  body_text:   {with_body}")
        logger.info(f"  cta_text:    {with_cta}")
        logger.info(f"  link_url:    {with_link}")
        logger.info(f"  display_url: {with_display}")
        logger.info(f"  images:      {with_image}")
        logger.info(f"  videos:      {with_video}")

        for i, c in enumerate(parsed_cards[:5]):
            logger.info(f"--- Card {i + 1} ---")
            logger.info(f"  id: {c.library_id}, page: {c.page_name}")
            logger.info(f"  cta: {c.cta_text}, link: {c.link_url}")
            logger.info(f"  page_url: {c.page_url}")
            logger.info(f"  display: {c.display_url}")
            logger.info(f"  body: {(c.body_text or '')[:100]}")
            logger.info(f"  imgs ({len(c.image_urls)}): {c.image_urls[:2]}")
            logger.info(f"  vids ({len(c.video_urls)}): {c.video_urls[:1]}")


if __name__ == "__main__":
    asyncio.run(main())