"""
Reconnaissance: can we get HD video/image by emulating player quality selection?

Steps:
  1. Open an ad via ?id= page (or feed) with a video ad
  2. Find the video player quality control (gear/settings)
  3. Emulate: click settings → select HD
  4. Compare video.src before vs after
  5. Monitor network requests during playback for HD manifests
  6. Check images on ?id= page vs feed (is there a larger version?)

Usage:
  docker exec spy_parser python3 -m app.probe_hd_player [keyword] [country] [ad_id]
  docker exec spy_parser python3 -m app.probe_hd_player casino MX
  docker exec spy_parser python3 -m app.probe_hd_player oxys PE 123456789
"""

import asyncio
import sys
from loguru import logger
from app.browser import browser_context, goto_with_challenge_retry, build_library_url
from app.db import AsyncSessionLocal
from sqlalchemy import text


VIDEO_STATE_JS = """
() => {
    const vids = Array.from(document.querySelectorAll('video'));
    return vids.slice(0, 4).map(v => ({
        src: v.src || '',
        cur: v.currentSrc || '',
        poster: v.poster || '',
        rs: v.readyState,
        w: v.videoWidth || 0,
        h: v.videoHeight || 0,
        sources: Array.from(v.querySelectorAll('source')).map(s => ({
            src: s.getAttribute('src') || '',
            type: s.type || '',
            label: s.getAttribute('label') || '',
        })),
    }));
}
"""

IMAGE_STATE_JS = """
() => {
    return Array.from(document.querySelectorAll('img'))
        .filter(i => i.src && i.src.includes('fbcdn') && !i.src.includes('s60x60'))
        .slice(0, 6)
        .map(i => ({
            src: i.src,
            w: i.naturalWidth,
            h: i.naturalHeight,
            hasStp: i.src.includes('stp='),
        }));
}
"""

QUALITY_CONTROLS_JS = """
() => {
    // Look for quality-related controls in the player
    const selectors = [
        '[aria-label*="ality"]',     // Quality, Качество
        '[aria-label*="HD"]',
        '[aria-label*="ettings"]',   // Settings
        '[aria-label*="astройки"]',  // Настройки
        '[data-sigil*="quality"]',
        'button[class*="quality"]',
        '[class*="QualityControl"]',
        '[class*="quality"]',
        '[class*="settings" i]',
        'svg[aria-label*="etting"]',
    ];
    const found = [];
    for (const sel of selectors) {
        try {
            const els = document.querySelectorAll(sel);
            for (const el of els) {
                found.push({
                    sel,
                    tag: el.tagName,
                    aria: el.getAttribute('aria-label') || '',
                    cls: (el.className || '').toString().slice(0, 80),
                    text: (el.innerText || '').trim().slice(0, 40),
                    rect: JSON.stringify(el.getBoundingClientRect()),
                });
            }
        } catch(e) {}
    }
    return found;
}
"""

QUALITY_MENU_JS = """
() => {
    // After clicking settings, look for quality menu items
    const keywords = ['HD', '720', '1080', '480', '360', 'SD', 'Auto', 'Авто',
                      'Качество', 'Quality', 'uality'];
    const found = [];
    for (const el of document.querySelectorAll('*')) {
        const t = (el.innerText || '').trim();
        if (t.length > 0 && t.length < 20) {
            for (const kw of keywords) {
                if (t.includes(kw)) {
                    found.push({
                        tag: el.tagName,
                        text: t,
                        aria: el.getAttribute('aria-label') || '',
                        cls: (el.className || '').toString().slice(0, 60),
                        role: el.getAttribute('role') || '',
                    });
                    break;
                }
            }
        }
    }
    return found.slice(0, 20);
}
"""


async def get_video_ad_id() -> str | None:
    """Get a library_id of a video ad from DB."""
    async with AsyncSessionLocal() as session:
        row = (await session.execute(text(
            "SELECT a.library_id FROM creatives c "
            "JOIN ads a ON a.id = c.ad_id "
            "WHERE c.media_type='VIDEO' AND c.s3_url IS NOT NULL "
            "ORDER BY c.id DESC LIMIT 1"
        ))).fetchone()
    return row[0] if row else None


async def probe_feed(page, keyword: str, country: str):
    """Probe the feed page for video quality controls."""
    url = build_library_url(country, keyword)
    logger.info(f"Opening feed: {url}")
    ok = await goto_with_challenge_retry(page, url)
    if not ok:
        logger.error("Failed to load feed")
        return

    try:
        await page.wait_for_selector('div:has-text("Library ID")', timeout=30_000)
    except Exception:
        pass
    await asyncio.sleep(4)

    # Scroll a few times to load video ads
    for i in range(4):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)

    vids = await page.evaluate(VIDEO_STATE_JS)
    logger.info(f"\nFeed: {len(vids)} video element(s) found")
    for i, v in enumerate(vids):
        logger.info(f"  [vid {i}] rs={v['rs']} {v['w']}x{v['h']}  src={v['src'][:80] or '(empty)'}")


async def probe_ad_page(page, ad_id: str):
    """Deep-probe a single ad via ?id= page."""
    url = f"https://www.facebook.com/ads/library/?id={ad_id}"
    logger.info(f"\nOpening ad page: {url}")

    # Set up network request capture
    video_requests: list[str] = []
    def on_request(req):
        u = req.url
        if any(x in u for x in ['/video/', '.mp4', 'videoplayback', '/v/', 'fbcdn.net/v']):
            video_requests.append(u)
    page.on('request', on_request)

    ok = await goto_with_challenge_retry(page, url)
    if not ok:
        logger.error("Failed to load ad page")
        return

    await asyncio.sleep(5)

    # ── Images ────────────────────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("IMAGES on ?id= page")
    logger.info("=" * 60)
    imgs = await page.evaluate(IMAGE_STATE_JS)
    if not imgs:
        logger.warning("  No fbcdn images found")
    for img in imgs:
        stp = "stp=" in img['src']
        stp_val = ""
        if stp:
            import re
            m = re.search(r'stp=([^&]+)', img['src'])
            stp_val = m.group(1) if m else ""
        logger.info(f"  {img['w']}x{img['h']}  stp={stp}  stp_val={stp_val}  url={img['src'][:100]}")

    # ── Videos before play ───────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("VIDEOS before play")
    logger.info("=" * 60)
    vids_before = await page.evaluate(VIDEO_STATE_JS)
    for i, v in enumerate(vids_before):
        logger.info(f"  [vid {i}] rs={v['rs']} {v['w']}x{v['h']}")
        logger.info(f"    src={v['src'][:100] or '(empty)'}")
        logger.info(f"    cur={v['cur'][:100] or '(empty)'}")
        for s in v['sources']:
            logger.info(f"    <source> label={s['label']!r} type={s['type']!r} src={s['src'][:80]}")

    # ── Quality controls ─────────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("QUALITY CONTROLS (before play)")
    logger.info("=" * 60)
    controls = await page.evaluate(QUALITY_CONTROLS_JS)
    if not controls:
        logger.warning("  No quality controls found in DOM")
    for c in controls:
        logger.info(f"  sel={c['sel']}  tag={c['tag']}  aria={c['aria']!r}  text={c['text']!r}")

    # ── Trigger play ─────────────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("TRIGGERING PLAY on all videos")
    logger.info("=" * 60)
    n = await page.evaluate("""
    async () => {
        const vids = document.querySelectorAll('video');
        for (const v of vids) { try { await v.play(); } catch(e) {} }
        return vids.length;
    }
    """)
    logger.info(f"  play() triggered on {n} video(s)")
    await asyncio.sleep(5)

    # ── Videos after play ────────────────────────────────────────────────────
    vids_after = await page.evaluate(VIDEO_STATE_JS)
    logger.info("\nVIDEOS after 5s play:")
    for i, v in enumerate(vids_after):
        logger.info(f"  [vid {i}] rs={v['rs']} {v['w']}x{v['h']}")
        logger.info(f"    src={v['src'][:100] or '(empty)'}")
        logger.info(f"    cur={v['cur'][:100] or '(empty)'}")

    # ── Quality controls after play ──────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("QUALITY CONTROLS (after play)")
    logger.info("=" * 60)
    controls2 = await page.evaluate(QUALITY_CONTROLS_JS)
    if not controls2:
        logger.warning("  Still no quality controls")
    for c in controls2:
        logger.info(f"  sel={c['sel']}  tag={c['tag']}  aria={c['aria']!r}  text={c['text']!r}")

    # ── Try clicking each quality control ────────────────────────────────────
    if controls2:
        logger.info("\nAttempting to click quality controls...")
        for c in controls2[:3]:
            try:
                sel = c['sel']
                el = page.locator(sel).first
                await el.scroll_into_view_if_needed()
                await el.click()
                logger.info(f"  Clicked: {c['aria']!r} / {c['text']!r}")
                await asyncio.sleep(2)

                # Check if quality menu appeared
                menu = await page.evaluate(QUALITY_MENU_JS)
                if menu:
                    logger.info("  Quality menu items found:")
                    for m in menu:
                        logger.info(f"    tag={m['tag']}  text={m['text']!r}  aria={m['aria']!r}")

                    # Try clicking HD option
                    for item in menu:
                        if any(kw in item['text'] for kw in ['HD', '720', '1080']):
                            logger.info(f"  → Clicking HD option: {item['text']!r}")
                            try:
                                hd_el = page.get_by_text(item['text'], exact=True).first
                                await hd_el.click()
                                await asyncio.sleep(3)

                                vids_hd = await page.evaluate(VIDEO_STATE_JS)
                                logger.info("\n  AFTER HD SELECTION:")
                                for vi, v in enumerate(vids_hd):
                                    before_src = vids_after[vi]['src'] if vi < len(vids_after) else ''
                                    after_src = v['src']
                                    changed = "CHANGED" if before_src != after_src else "same"
                                    logger.info(f"    [vid {vi}] rs={v['rs']} {v['w']}x{v['h']}  src={changed}")
                                    if before_src != after_src:
                                        logger.info(f"      before: {before_src[:100]}")
                                        logger.info(f"      after:  {after_src[:100]}")
                            except Exception as e:
                                logger.warning(f"  Failed to click HD: {e}")
                            break
                else:
                    logger.info("  No quality menu appeared after click")
            except Exception as e:
                logger.warning(f"  Click failed for {c['sel']!r}: {e}")

    # ── Network video requests ────────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("NETWORK VIDEO REQUESTS captured")
    logger.info("=" * 60)
    if not video_requests:
        logger.warning("  No video network requests captured")
    for req_url in video_requests[:10]:
        logger.info(f"  {req_url[:120]}")

    # ── Screenshot for manual inspection ─────────────────────────────────────
    await page.screenshot(path="/tmp/probe_hd_player.png", full_page=False)
    logger.info("\nScreenshot saved: /tmp/probe_hd_player.png")
    logger.info("Copy with: docker cp spy_parser:/tmp/probe_hd_player.png ~/Desktop/")


async def main():
    keyword = sys.argv[1] if len(sys.argv) > 1 else "oxys"
    country = sys.argv[2] if len(sys.argv) > 2 else "PE"
    ad_id = sys.argv[3] if len(sys.argv) > 3 else None

    if not ad_id:
        ad_id = await get_video_ad_id()
        if ad_id:
            logger.info(f"Using video ad from DB: {ad_id}")
        else:
            logger.warning("No video ad found in DB, will use feed only")

    async with browser_context() as ctx:
        page = await ctx.new_page()

        if ad_id:
            await probe_ad_page(page, ad_id)
        else:
            await probe_feed(page, keyword, country)

    logger.info("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
