"""
Verification probe: confirm HD image uplift + video quality + no regression.

Checks:
  1. Image dimensions before/after stp-fix (from creatives table + live HTTP HEAD)
  2. Video file sizes from DB + ffprobe resolution if available
  3. Regression: scroll oxys/PE and print card stats
"""

import asyncio
import subprocess
import sys
import httpx
from loguru import logger
from app.db import AsyncSessionLocal
from app.models import Creative, AdMediaType


# ── 1. DB stats: image dimensions old vs recent ───────────────────────────────

IMG_STATS_SQL = """
SELECT
    width, height, file_size,
    created_at
FROM creatives
WHERE media_type = 'IMAGE'
  AND width IS NOT NULL
ORDER BY id ASC
LIMIT 20;
"""

IMG_STATS_SQL_RECENT = """
SELECT
    width, height, file_size,
    created_at
FROM creatives
WHERE media_type = 'IMAGE'
  AND width IS NOT NULL
ORDER BY id DESC
LIMIT 20;
"""

IMG_URL_SAMPLE = """
SELECT original_url, width, height, file_size
FROM creatives
WHERE media_type = 'IMAGE'
  AND original_url LIKE '%fbcdn.net%'
  AND width IS NOT NULL
ORDER BY id DESC
LIMIT 3;
"""

VIDEO_STATS_SQL = """
SELECT file_size, s3_url, created_at
FROM creatives
WHERE media_type = 'VIDEO'
  AND file_size IS NOT NULL
ORDER BY id DESC
LIMIT 10;
"""


async def check_db_dimensions():
    logger.info("=" * 60)
    logger.info("1. IMAGE DIMENSIONS: oldest 20 vs newest 20 in DB")
    logger.info("=" * 60)

    from sqlalchemy import text
    async with AsyncSessionLocal() as session:
        rows_old = (await session.execute(text(IMG_STATS_SQL))).fetchall()
        rows_new = (await session.execute(text(IMG_STATS_SQL_RECENT))).fetchall()
        urls = (await session.execute(text(IMG_URL_SAMPLE))).fetchall()
        vids = (await session.execute(text(VIDEO_STATS_SQL))).fetchall()

    def stats(rows):
        widths = [r.width for r in rows if r.width]
        heights = [r.height for r in rows if r.height]
        sizes = [r.file_size for r in rows if r.file_size]
        if not widths:
            return "no data"
        return (
            f"w: {min(widths)}–{max(widths)} avg={sum(widths)//len(widths)}  "
            f"h: {min(heights)}–{max(heights)} avg={sum(heights)//len(heights)}  "
            f"size: {min(sizes)//1024}–{max(sizes)//1024} KB avg={sum(sizes)//len(sizes)//1024} KB"
        )

    logger.info(f"OLDEST 20 images: {stats(rows_old)}")
    logger.info(f"NEWEST 20 images: {stats(rows_new)}")

    logger.info("\nSample recent image URLs + stored dimensions:")
    for r in urls:
        stp_in_url = "stp=" in r.original_url
        logger.info(
            f"  {r.width}×{r.height}  {r.file_size // 1024} KB  "
            f"stp_in_url={stp_in_url}  url={r.original_url[:80]}"
        )

    logger.info("\nRecent VIDEO file sizes:")
    if not vids:
        logger.warning("  No video rows found")
    for v in vids:
        size_mb = (v.file_size or 0) / 1_048_576
        logger.info(f"  {size_mb:.2f} MB  {v.s3_url[:80] if v.s3_url else '(no url)'}")

    return urls


# ── 2. Live HTTP HEAD: with vs without stp ────────────────────────────────────

async def check_live_url(original_url: str):
    logger.info("")
    logger.info("=" * 60)
    logger.info("2. LIVE HTTP HEAD: with stp vs without stp")
    logger.info("=" * 60)

    from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

    parsed = urlparse(original_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    has_stp = "stp" in params

    if not has_stp:
        # reconstruct URL with stp to simulate old behavior
        stp_url = original_url
        parts = list(parsed)
        query_with_stp = parsed.query + ("&" if parsed.query else "") + "stp=dst-jpg_s600x600_tt6"
        parts[4] = query_with_stp
        stp_url = urlunparse(parts)
        no_stp_url = original_url
    else:
        stp_url = original_url
        params.pop("stp")
        flat = "&".join(f"{k}={v[0]}" for k, v in params.items())
        parts = list(parsed)
        parts[4] = flat
        no_stp_url = urlunparse(parts)

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for label, url in [("with stp (old)", stp_url), ("no stp (new)", no_stp_url)]:
            try:
                r = await client.head(url)
                cl = r.headers.get("content-length", "unknown")
                ct = r.headers.get("content-type", "")
                kb = int(cl) // 1024 if cl.isdigit() else "?"
                logger.info(f"  {label}: HTTP {r.status_code}  size={kb} KB  type={ct}")
            except Exception as e:
                logger.warning(f"  {label}: ERROR {e}")


# ── 3. ffprobe video resolution ───────────────────────────────────────────────

def check_video_resolution(s3_url: str):
    logger.info("")
    logger.info("=" * 60)
    logger.info("3. VIDEO RESOLUTION via ffprobe")
    logger.info("=" * 60)

    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,bit_rate",
                "-of", "default=noprint_wrappers=1",
                s3_url,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info(f"  {result.stdout.strip()}")
        else:
            logger.warning(f"  ffprobe error: {result.stderr[:200]}")
    except FileNotFoundError:
        logger.warning("  ffprobe not installed — skipping video resolution check")
    except Exception as e:
        logger.warning(f"  ffprobe failed: {e}")


# ── 4. Regression: scroll and parse existing config ───────────────────────────

async def check_regression():
    logger.info("")
    logger.info("=" * 60)
    logger.info("4. REGRESSION: scroll oxys/PE, check card stats")
    logger.info("=" * 60)

    from app.browser import browser_context, goto_with_challenge_retry, build_library_url
    from app.parsers.library_extractor import scroll_and_collect
    from app.parsers.library_card import parse_card_text

    keyword = sys.argv[1] if len(sys.argv) > 1 else "oxys"
    country = sys.argv[2] if len(sys.argv) > 2 else "PE"
    url = build_library_url(country, keyword)
    logger.info(f"  Opening {url}")

    async with browser_context() as ctx:
        page = await ctx.new_page()
        ok = await goto_with_challenge_retry(page, url)
        if not ok:
            logger.error("  Page failed to load")
            return
        try:
            await page.wait_for_selector('div:has-text("Library ID")', timeout=30_000)
        except Exception:
            pass
        await asyncio.sleep(4)

        raw_cards = await scroll_and_collect(page, max_scrolls=5)

    with_img = with_vid = with_both = with_none = 0
    for raw in raw_cards:
        card = parse_card_text(raw["text"])
        imgs = [i["src"] for i in raw["images"]]
        vids = [v["src"] for v in raw["videos"] if v["src"]]
        if imgs and vids:
            with_both += 1
        elif imgs:
            with_img += 1
        elif vids:
            with_vid += 1
        else:
            with_none += 1

    total = len(raw_cards)
    logger.info(f"  Total cards: {total}")
    logger.info(f"  image only:  {with_img}")
    logger.info(f"  video only:  {with_vid}")
    logger.info(f"  both:        {with_both}")
    logger.info(f"  neither:     {with_none}  ← should be low")

    # Show a few image URLs to confirm no stp
    for raw in raw_cards[:3]:
        for img in raw["images"][:1]:
            stp = "stp=" in img["src"]
            logger.info(f"  img stp_present={stp}  {img['src'][:100]}")


async def main():
    url_rows = await check_db_dimensions()

    if url_rows:
        await check_live_url(url_rows[0].original_url)

    # Video ffprobe — use first video from DB
    from sqlalchemy import text
    async with AsyncSessionLocal() as session:
        vid_row = (await session.execute(text(
            "SELECT s3_url FROM creatives WHERE media_type='VIDEO' AND s3_url IS NOT NULL ORDER BY id DESC LIMIT 1"
        ))).fetchone()
    if vid_row:
        check_video_resolution(vid_row[0])

    await check_regression()
    logger.info("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
