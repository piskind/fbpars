"""
Diagnostic: inspect HD availability in FB Ad Library DOM.

Prints for the first ad card found:
  - image: src, full srcset attribute, naturalWidth/Height
  - video: all <source> children (src, type, label) BEFORE play
  - video: same AFTER play + a 3s wait
"""

import asyncio
from loguru import logger
from app.browser import browser_context, goto_with_challenge_retry, build_library_url


PROBE_IMAGES = """
() => {
    const results = [];
    const all = document.querySelectorAll('div');
    for (const el of all) {
        const txt = el.innerText || '';
        if (!txt.includes('Library ID:')) continue;
        if (!txt.includes('Sponsored')) continue;
        if (txt.length > 5000 || txt.length < 100) continue;

        const idx = txt.indexOf('Library ID:');
        const after = txt.slice(idx + 'Library ID:'.length).trim();
        if (!/^\\d+/.test(after)) continue;

        const imgs = Array.from(el.querySelectorAll('img'))
            .filter(i => i.src && i.src.startsWith('http') &&
                !i.src.includes('s60x60') && !i.src.includes('p60x60') &&
                !i.src.includes('emoji.php') && !i.src.includes('rsrc.php'))
            .map(i => ({
                src: i.src,
                srcset: i.getAttribute('srcset') || i.srcset || '',
                w: i.naturalWidth,
                h: i.naturalHeight,
            }));

        if (imgs.length > 0) return imgs.slice(0, 3);
    }
    return [];
}
"""

PROBE_VIDEOS_BEFORE = """
() => {
    const results = [];
    const all = document.querySelectorAll('div');
    for (const el of all) {
        const txt = el.innerText || '';
        if (!txt.includes('Library ID:')) continue;
        if (!txt.includes('Sponsored')) continue;
        if (txt.length > 5000 || txt.length < 100) continue;

        const idx = txt.indexOf('Library ID:');
        const after = txt.slice(idx + 'Library ID:'.length).trim();
        if (!/^\\d+/.test(after)) continue;

        const vids = Array.from(el.querySelectorAll('video'));
        for (const v of vids) {
            const sources = Array.from(v.querySelectorAll('source')).map(s => ({
                src: s.src || s.getAttribute('src') || '',
                type: s.type || s.getAttribute('type') || '',
                label: s.getAttribute('label') || '',
                res: s.getAttribute('res') || s.getAttribute('data-res') || '',
            }));
            results.push({
                src: v.src || '',
                currentSrc: v.currentSrc || '',
                poster: v.poster || '',
                readyState: v.readyState,
                sources,
            });
        }
        if (results.length > 0) return results.slice(0, 2);
    }
    return [];
}
"""

TRIGGER_PLAY = """
async () => {
    const vids = document.querySelectorAll('video');
    for (const v of vids) {
        try { await v.play(); } catch(e) {}
    }
    return vids.length;
}
"""

PROBE_VIDEOS_AFTER = """
() => {
    const results = [];
    const vids = document.querySelectorAll('video');
    for (const v of vids) {
        const sources = Array.from(v.querySelectorAll('source')).map(s => ({
            src: s.src || s.getAttribute('src') || '',
            type: s.type || s.getAttribute('type') || '',
            label: s.getAttribute('label') || '',
            res: s.getAttribute('res') || s.getAttribute('data-res') || '',
        }));
        results.push({
            src: v.src || '',
            currentSrc: v.currentSrc || '',
            poster: v.poster || '',
            readyState: v.readyState,
            sources,
        });
    }
    return Array.from(results).slice(0, 2);
}
"""


async def main():
    import sys
    keyword = sys.argv[1] if len(sys.argv) > 1 else "oxys"
    country = sys.argv[2] if len(sys.argv) > 2 else "PE"
    url = build_library_url(country, keyword)
    logger.info(f"Opening: {url}")

    async with browser_context() as context:
        page = await context.new_page()
        ok = await goto_with_challenge_retry(page, url)
        if not ok:
            logger.error("Failed to load page (challenge not cleared)")
            return

        try:
            await page.wait_for_selector('div:has-text("Library ID")', timeout=30_000)
        except Exception:
            logger.warning("Timeout waiting for Library ID — trying anyway")
        await asyncio.sleep(4)

        # ── IMAGES ────────────────────────────────────────────────────────────
        logger.info("=" * 60)
        logger.info("IMAGES — src + srcset + naturalSize")
        logger.info("=" * 60)
        imgs = await page.evaluate(PROBE_IMAGES)
        if not imgs:
            logger.warning("No matching images found")
        for i, img in enumerate(imgs):
            logger.info(f"\n[img {i}]")
            logger.info(f"  src  : {img['src'][:120]}")
            logger.info(f"  w×h  : {img['w']}×{img['h']}")
            srcset = img['srcset']
            if srcset:
                logger.info("  srcset entries:")
                for part in srcset.split(','):
                    logger.info(f"    {part.strip()[:150]}")
            else:
                logger.info("  srcset: (empty)")

        # ── SCROLL to load more cards ─────────────────────────────────────────
        logger.info("\nScrolling to load more cards (3 scrolls) …")
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(3)

        # ── GLOBAL VIDEO SCAN (all <video> on page) ───────────────────────────
        logger.info("")
        logger.info("=" * 60)
        logger.info("GLOBAL <video> scan — all videos on page after scroll")
        logger.info("=" * 60)
        global_vids = await page.evaluate("""() => {
            const vids = Array.from(document.querySelectorAll('video'));
            return {
                total: vids.length,
                items: vids.slice(0, 6).map(v => ({
                    src: v.src || '',
                    cur: v.currentSrc || '',
                    poster: v.poster || '',
                    rs: v.readyState,
                    sources: Array.from(v.querySelectorAll('source')).map(s => ({
                        src: s.getAttribute('src') || '',
                        type: s.type || '',
                        label: s.getAttribute('label') || '',
                        res: s.getAttribute('res') || ''
                    }))
                }))
            };
        }""")
        logger.info(f"Total <video> elements on page: {global_vids['total']}")
        for i, v in enumerate(global_vids['items']):
            logger.info(f"\n[vid {i}] readyState={v['rs']}")
            logger.info(f"  src   : {v['src'][:100] or '(empty)'}")
            logger.info(f"  cur   : {v['cur'][:100] or '(empty)'}")
            logger.info(f"  poster: {v['poster'][:80] or '(empty)'}")
            if v['sources']:
                logger.info("  <source> elements:")
                for s in v['sources']:
                    logger.info(f"    label={s['label']!r:6} type={s['type']!r:20} res={s['res']!r:4} src={s['src'][:80]}")
            else:
                logger.info("  <source>: none")

        if global_vids['total'] == 0:
            logger.warning("No <video> found at all — try a different keyword/country")
            return

        # ── TRIGGER PLAY on all ───────────────────────────────────────────────
        n = await page.evaluate(TRIGGER_PLAY)
        logger.info(f"\nTriggered play() on {n} video(s) — waiting 4s …")
        await asyncio.sleep(4)

        # ── VIDEOS AFTER PLAY ─────────────────────────────────────────────────
        logger.info("")
        logger.info("=" * 60)
        logger.info("GLOBAL <video> AFTER play() + 4s wait")
        logger.info("=" * 60)
        vids_after = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('video')).slice(0, 6).map(v => ({
                src: v.src || '',
                cur: v.currentSrc || '',
                rs: v.readyState,
                sources: Array.from(v.querySelectorAll('source')).map(s => ({
                    src: s.getAttribute('src') || '',
                    type: s.type || '',
                    label: s.getAttribute('label') || '',
                    res: s.getAttribute('res') || ''
                }))
            }));
        }""")
        for i, v in enumerate(vids_after):
            logger.info(f"\n[vid {i}] readyState={v['rs']}")
            logger.info(f"  src: {v['src'][:100] or '(empty)'}")
            logger.info(f"  cur: {v['cur'][:100] or '(empty)'}")
            if v['sources']:
                logger.info("  <source> elements:")
                for s in v['sources']:
                    logger.info(f"    label={s['label']!r:6} type={s['type']!r:20} res={s['res']!r:4} src={s['src'][:80]}")
            else:
                logger.info("  <source>: none")

        logger.info("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
