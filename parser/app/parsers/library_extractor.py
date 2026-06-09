import asyncio
from datetime import datetime
from playwright.async_api import Page
from loguru import logger


SCROLL_SCRIPT = """
async () => {
    return new Promise((resolve) => {
        window.scrollTo(0, document.body.scrollHeight);
        setTimeout(() => resolve(document.body.scrollHeight), 2000);
    });
}
"""


EXTRACT_SCRIPT = """
() => {
    const seen = new Set();
    const results = [];
    const all = document.querySelectorAll('div');

    const decodeFbRedirect = (href) => {
        try {
            const url = new URL(href);
            if (url.hostname.endsWith('facebook.com') && url.pathname === '/l.php') {
                const u = url.searchParams.get('u');
                return u ? decodeURIComponent(u) : null;
            }
        } catch (e) {}
        return null;
    };

    const PLATFORM_KEYWORDS = ['Facebook', 'Instagram', 'Messenger', 'Audience Network', 'Threads'];
    const extractPlatforms = (el) => {
        const found = new Set();
        // 1) текстовая строка "Platforms"
        const txt = el.innerText || '';
        const m = txt.match(/Platforms\\s*[:\\n]\\s*([^\\n]+)/i);
        if (m) {
            for (const kw of PLATFORM_KEYWORDS) {
                if (m[1].includes(kw)) found.add(kw);
            }
        }
        // 2) aria-label / alt у иконок
        const labelled = el.querySelectorAll('[aria-label], img[alt]');
        for (const node of labelled) {
            const label = (node.getAttribute('aria-label') || node.getAttribute('alt') || '').trim();
            for (const kw of PLATFORM_KEYWORDS) {
                if (label === kw || label.includes(kw)) found.add(kw);
            }
        }
        return Array.from(found);
    };

    for (const el of all) {
        const txt = el.innerText || '';
        if (!txt.includes('Library ID:')) continue;
        if (!txt.includes('Sponsored')) continue;
        if (txt.length > 5000) continue;
        if (txt.length < 100) continue;

        const idx = txt.indexOf('Library ID:');
        const after = txt.slice(idx + 'Library ID:'.length).trim();
        const idMatch = after.match(/^(\\d+)/);
        if (!idMatch) continue;
        const id = idMatch[1];
        if (seen.has(id)) continue;

        const idCount = (txt.match(/Library ID:/g) || []).length;
        if (idCount > 1) continue;

        seen.add(id);

        const imgs = Array.from(el.querySelectorAll('img'))
            .map(i => ({
                src: i.src,
                w: i.naturalWidth || 0,
                h: i.naturalHeight || 0,
                alt: i.alt || ''
            }))
            .filter(i => {
                if (!i.src || !i.src.startsWith('http')) return false;
                if (i.src.includes('s60x60')) return false;
                if (i.src.includes('p60x60')) return false;
                if (i.src.includes('emoji.php')) return false;
                if (i.src.includes('rsrc.php')) return false;
                return true;
            });

        const vids = Array.from(el.querySelectorAll('video'))
            .map(v => ({src: v.src || v.currentSrc, poster: v.poster}))
            .filter(v => v.src || v.poster);

        const anchors = Array.from(el.querySelectorAll('a[href]'));
        let externalUrl = null;
        let pageUrl = null;

        for (const a of anchors) {
            const href = a.href;
            if (!href) continue;

            const decoded = decodeFbRedirect(href);
            if (decoded) {
                externalUrl = externalUrl || decoded;
                continue;
            }

            if (href.includes('facebook.com/ads/library')) continue;

            const pageMatch = href.match(/facebook\\.com\\/(\\d+)/);
            if (pageMatch && !pageUrl) {
                pageUrl = href;
            }

            if (!href.includes('facebook.com') && !externalUrl) {
                externalUrl = href;
            }
        }

        const platforms = extractPlatforms(el);

        results.push({
            text: txt,
            images: imgs,
            videos: vids,
            page_url: pageUrl,
            external_url: externalUrl,
            platforms: platforms,
        });
    }
    return results;
}
"""


COUNT_SCRIPT = """
() => {
    const seen = new Set();
    const all = document.querySelectorAll('div');
    for (const el of all) {
        const txt = el.innerText || '';
        if (!txt.includes('Library ID:')) continue;
        if (!txt.includes('Sponsored')) continue;
        if (txt.length > 5000) continue;
        if (txt.length < 100) continue;
        const idx = txt.indexOf('Library ID:');
        const after = txt.slice(idx + 'Library ID:'.length).trim();
        const idMatch = after.match(/^(\\d+)/);
        if (!idMatch) continue;
        const id = idMatch[1];
        if (seen.has(id)) continue;
        const idCount = (txt.match(/Library ID:/g) || []).length;
        if (idCount > 1) continue;
        seen.add(id);
    }
    return {count: seen.size, sample: Array.from(seen).slice(0, 5)};
}
"""

FB_RESULTS_LABEL_SCRIPT = """
() => {
    const el = [...document.querySelectorAll('*')].find(
        e => /result|результат/i.test(e.innerText) && e.innerText.length < 50
    );
    return el ? el.innerText.trim() : 'not found';
}
"""

MEMORY_SCRIPT = """
() => {
    if (!performance || !performance.memory) return null;
    return {
        used_mb: Math.round(performance.memory.usedJSHeapSize / 1048576),
        total_mb: Math.round(performance.memory.totalJSHeapSize / 1048576),
    };
}
"""


async def scroll_and_count(
    page: Page,
    max_scrolls: int = 200,
    stable_rounds: int = 7,
    log_every: int = 10,
) -> int:
    """
    Scroll Ad Library without extracting card data — counts unique Library IDs
    visible in the DOM at each step.

    FB's virtual DOM evicts old cards during long scrolls, so the counter may
    drop. Eviction events are logged explicitly. The goal is to measure scroll
    throughput (ads/min) and find the DOM retention ceiling, not get a total count.
    """
    started = datetime.now()
    prev_count = 0
    peak_count = 0
    stable = 0
    last_height = 0

    fb_label = await page.evaluate(FB_RESULTS_LABEL_SCRIPT)
    logger.info(f"[count] FB results label: {fb_label!r}")

    for i in range(max_scrolls):
        new_height = await page.evaluate(SCROLL_SCRIPT)
        height_delta = new_height - last_height
        result = await page.evaluate(COUNT_SCRIPT)
        current_count = result["count"]
        current_sample = result["sample"]

        if i == 0 and current_sample:
            logger.info(f"[count] sample Library IDs at scroll 1: {current_sample}")

        if current_count < prev_count:
            logger.warning(
                f"[count] DOM eviction detected at scroll {i + 1}, "
                f"count dropped {prev_count}→{current_count}"
            )

        if current_count > peak_count:
            peak_count = current_count

        should_log = (i + 1) % log_every == 0 or current_count != prev_count
        if should_log:
            elapsed = (datetime.now() - started).total_seconds()
            rate = peak_count / elapsed * 60 if elapsed > 0 else 0
            mem = await page.evaluate(MEMORY_SCRIPT)
            mem_str = f" | mem={mem['used_mb']}/{mem['total_mb']}MB" if mem else ""
            logger.info(
                f"[count] scroll={i + 1} | height_delta={height_delta:+} | "
                f"dom={current_count} | peak={peak_count} | "
                f"elapsed={elapsed:.0f}s | rate={rate:.0f} ads/min{mem_str}"
            )

        if current_count == prev_count and new_height == last_height:
            stable += 1
            if stable >= stable_rounds:
                logger.info(
                    f"[count] End of feed at scroll {i + 1} — "
                    f"height stable for {stable_rounds} consecutive rounds "
                    f"(FB stopped loading new content)"
                )
                break
        else:
            stable = 0

        prev_count = current_count
        last_height = new_height
        await asyncio.sleep(1.5)

    elapsed = (datetime.now() - started).total_seconds()
    rate = peak_count / elapsed * 60 if elapsed > 0 else 0
    logger.info(
        f"[count] DONE | dom_final={prev_count} | peak={peak_count} | "
        f"time={elapsed:.0f}s ({elapsed / 60:.1f}min) | rate={rate:.0f} ads/min"
    )
    return peak_count


async def scroll_and_collect(
    page: Page,
    max_scrolls: int = 30,
    stable_rounds: int = 3,
) -> list[dict]:
    seen_count = 0
    stable = 0
    last_height = 0

    for i in range(max_scrolls):
        new_height = await page.evaluate(SCROLL_SCRIPT)
        cards = await page.evaluate(EXTRACT_SCRIPT)
        current_count = len(cards)

        logger.info(f"Scroll {i + 1}/{max_scrolls}: height={new_height}, cards={current_count}")

        if current_count == seen_count and new_height == last_height:
            stable += 1
            if stable >= stable_rounds:
                logger.info(f"Reached end of feed after {i + 1} scrolls")
                break
        else:
            stable = 0

        seen_count = current_count
        last_height = new_height
        await asyncio.sleep(1.5)

    final_cards = await page.evaluate(EXTRACT_SCRIPT)
    logger.info(f"Total extracted: {len(final_cards)} cards")
    return final_cards
