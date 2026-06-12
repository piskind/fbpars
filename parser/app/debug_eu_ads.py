"""
Reconnaissance script: dump EU transparency data for specific ad IDs.
Saves innerText, HTML and screenshot for each ad.

Usage:
    python -m app.debug_eu_ads
    python -m app.debug_eu_ads --ids=1930480227653290,2304290936647630
"""
import asyncio
import argparse
import re
from pathlib import Path
from loguru import logger

from app.browser import browser_context

OUTPUT_DIR = Path("/app")

DEFAULT_IDS = [
    "1930480227653290",  # IT, Prosta Vital
    "2304290936647630",  # ES, Pure Balance
    "2070741114322180",  # IT, Whole food nutrition
]

# Keywords to search for in any language — EU transparency signals
KEYWORDS = [
    r"охват", r"reach", r"impression",
    r"показ", r"показан",
    r"спенд", r"spent", r"spend", r"потрачено",
    r"€", r"\$",
    r"возраст", r"age",
    r"пол", r"gender",
    r"мужчин", r"женщин", r"male", r"female",
    r"страна", r"country", r"paes", r"paese", r"país",
    r"\d{1,3}[,\s]\d{3}",       # any number with thousands separator
    r"\d+\s*%",                  # percentages
    r"18[-–]\d{2}",              # age ranges like 18-24
    r"transparency", r"trasparenza", r"transparencia",
    r"about this ad", r"informazioni sull", r"información sobre",
    r"distribution", r"distribuzione", r"distribución",
]

# Button/link texts that reveal EU data (multi-language)
EXPAND_TEXTS = [
    "See ad details",
    "Ad details",
    "About this ad",
    "Informazioni sull'inserzione",
    "Información del anuncio",
    "Información sobre el anuncio",
    "Dati sull'inserzione",
    "Ver detalles del anuncio",
    "Ver los detalles del anuncio",
    "Dettagli dell'inserzione",
    "Рекламная библиотека",
    "Подробнее",
    "See details",
    "Details",
]


async def try_expand(page) -> bool:
    """Try to click any EU-transparency expand button. Returns True if clicked."""
    for text in EXPAND_TEXTS:
        try:
            loc = page.get_by_text(text, exact=False)
            count = await loc.count()
            if count > 0:
                logger.info(f"  Found expand button: {text!r} ({count} matches), clicking first")
                await loc.first.click(timeout=5_000)
                await asyncio.sleep(2)
                return True
        except Exception:
            pass

    # Fallback: click any <a> or <button> that looks like a details link
    try:
        details_link = page.locator('a[href*="ad_detail"], button:has-text("detail")')
        if await details_link.count() > 0:
            await details_link.first.click(timeout=5_000)
            await asyncio.sleep(2)
            return True
    except Exception:
        pass

    return False


async def find_eu_block(page) -> tuple[str, str]:
    """
    Try to find the EU transparency block.
    Returns (innerText, outerHTML) of the best candidate element,
    or (full_page_text, '') if nothing specific found.
    """
    # Candidate selectors — FB often puts EU data in a dialog or a dedicated section
    selectors = [
        '[role="dialog"]',
        '[aria-label*="detail" i]',
        '[aria-label*="dettagl" i]',
        '[aria-label*="detalle" i]',
        '[data-testid*="ad_detail"]',
    ]

    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                text = await el.inner_text(timeout=3_000)
                html = await el.inner_html(timeout=3_000)
                if len(text.strip()) > 50:
                    logger.info(f"  Found EU block via selector: {sel!r}")
                    return text, html
        except Exception:
            pass

    # Last resort: full page text
    try:
        text = await page.inner_text("body")
        return text, ""
    except Exception:
        return "", ""


def find_keywords(text: str) -> list[str]:
    found = []
    for pattern in KEYWORDS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            found.append(f"{pattern!r}: {matches[:5]}")
    return found


async def process_ad(context, ad_id: str) -> None:
    url = f"https://www.facebook.com/ads/library/?id={ad_id}"
    logger.info(f"\n{'='*60}")
    logger.info(f"Processing ad {ad_id}")
    logger.info(f"URL: {url}")

    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        # Check for anti-bot
        html_initial = await page.content()
        if "__rd_verify" in html_initial:
            logger.warning(f"  __rd_verify challenge for {ad_id} — skipping")
            return

        # Wait for ad content
        try:
            await page.wait_for_selector(
                'div:has-text("Library ID"), div:has-text("Sponsored"), [role="main"]',
                timeout=20_000,
            )
        except Exception:
            logger.warning(f"  No main content found for {ad_id}")

        await asyncio.sleep(3)

        # Screenshot BEFORE expanding
        pre_path = OUTPUT_DIR / f"debug_eu_{ad_id}_pre.png"
        await page.screenshot(path=str(pre_path), full_page=True)
        logger.info(f"  Screenshot saved: {pre_path}")

        # Try to expand EU transparency section
        expanded = await try_expand(page)
        if expanded:
            logger.info(f"  Expanded EU section")
            await asyncio.sleep(2)
            # Screenshot AFTER expanding
            post_path = OUTPUT_DIR / f"debug_eu_{ad_id}.png"
            await page.screenshot(path=str(post_path), full_page=True)
            logger.info(f"  Post-expand screenshot: {post_path}")
        else:
            logger.info(f"  No expand button found — dumping page as-is")
            pre_path.rename(OUTPUT_DIR / f"debug_eu_{ad_id}.png")

        # Extract EU block
        eu_text, eu_html = await find_eu_block(page)

        # Full page text for keyword search
        try:
            full_text = await page.inner_text("body")
        except Exception:
            full_text = eu_text

        # Keyword hits
        hits = find_keywords(full_text)

        # Write dump
        dump_path = OUTPUT_DIR / f"debug_eu_{ad_id}.txt"
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(f"AD ID: {ad_id}\n")
            f.write(f"URL: {url}\n")
            f.write(f"Expanded: {expanded}\n")
            f.write(f"\n{'─'*60}\n")
            f.write("KEYWORD HITS IN PAGE TEXT:\n")
            if hits:
                for h in hits:
                    f.write(f"  {h}\n")
            else:
                f.write("  (none found)\n")
            f.write(f"\n{'─'*60}\n")
            f.write("EU BLOCK innerText:\n")
            f.write(eu_text or "(empty)")
            f.write(f"\n\n{'─'*60}\n")
            f.write("EU BLOCK outerHTML:\n")
            f.write(eu_html or "(not extracted — see full page text below)")
            f.write(f"\n\n{'─'*60}\n")
            f.write("FULL PAGE innerText (body):\n")
            f.write(full_text or "(empty)")

        logger.info(f"  Dump saved: {dump_path}")
        logger.info(f"  Keyword hits: {len(hits)}")
        if hits:
            for h in hits[:10]:
                logger.info(f"    {h}")

    finally:
        await page.close()


async def run(ids: list[str]) -> None:
    logger.info(f"EU ad recon: {len(ids)} ads")
    async with browser_context() as context:
        for ad_id in ids:
            try:
                await process_ad(context, ad_id)
            except Exception as e:
                logger.error(f"Failed for {ad_id}: {e}")
            await asyncio.sleep(2)
    logger.info("Done. Files written to /app/debug_eu_*.txt and *.png")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--ids",
        default=",".join(DEFAULT_IDS),
        help="Comma-separated ad library IDs",
    )
    args = ap.parse_args()
    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    asyncio.run(run(ids))
