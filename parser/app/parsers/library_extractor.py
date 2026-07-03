import asyncio
import re
import time
from datetime import datetime
from playwright.async_api import Page
from loguru import logger

MAX_CARDS = 2500      # safety net; FB caps the feed well before this
_DEGRADE_FACTOR = 3   # scroll took N× median of first 5 → stop early
_DEGRADE_MIN_S = 10   # degrade only if scroll > this many seconds

_LIBRARY_ID_RE = re.compile(r"Library ID:\s*(\d+)")


def _merge_card(accumulated: dict, card: dict) -> bool:
    """
    Merge one card into the accumulator keyed by Library ID. Returns True if it's new.

    FB evicts off-screen cards, so one snapshot only holds ~20-30. We accumulate
    across scrolls and backfill media that rendered lazily.
    """
    m = _LIBRARY_ID_RE.search(card.get("text") or "")
    if not m:
        return False
    lib_id = m.group(1)
    existing = accumulated.get(lib_id)
    if existing is None:
        accumulated[lib_id] = card
        return True
    if not existing.get("images") and card.get("images"):
        existing["images"] = card["images"]
    if not existing.get("videos") and card.get("videos"):
        existing["videos"] = card["videos"]
    for key in ("external_url", "page_url"):
        if not existing.get(key) and card.get(key):
            existing[key] = card[key]
    return False


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

    const hdImgSrc = (src) => src;

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

    // Pre-pass: walk UP from each <video> to find its card (single Library-ID ancestor).
    // FB renders the video player in a sibling branch outside the text container,
    // so top-down querySelectorAll('video') from the text div finds nothing.
    const videoMap = new Map();
    for (const vid of document.querySelectorAll('video')) {
        if (!vid.src && !vid.currentSrc && !vid.poster) continue;
        let node = vid;
        for (let d = 0; d < 30; d++) {
            if (!node.parentElement) break;
            node = node.parentElement;
            const t = node.innerText || '';
            const cnt = (t.match(/Library ID:/g) || []).length;
            if (cnt === 0) continue;
            if (cnt === 1) {
                const m = t.slice(t.indexOf('Library ID:') + 'Library ID:'.length).trim().match(/^(\\d+)/);
                if (m) {
                    if (!videoMap.has(m[1])) videoMap.set(m[1], []);
                    videoMap.get(m[1]).push({
                        src: vid.src || vid.currentSrc || '',
                        poster: vid.poster || ''
                    });
                }
            }
            break; // cnt >= 1: stop regardless (going higher only adds more IDs)
        }
    }

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
            .filter(i => {
                if (!i.src || !i.src.startsWith('http')) return false;
                if (i.src.includes('s60x60')) return false;
                if (i.src.includes('p60x60')) return false;
                if (i.src.includes('emoji.php')) return false;
                if (i.src.includes('rsrc.php')) return false;
                return true;
            })
            .map(i => ({
                src: hdImgSrc(i.src),
                w: i.naturalWidth || 0,
                h: i.naturalHeight || 0,
                alt: i.alt || ''
            }));

        const vids = (videoMap.get(id) || []).filter(v => v.src || v.poster);

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

        if (!externalUrl) {
            for (const node of el.querySelectorAll('[data-lynx-uri], [data-store]')) {
                const uri = node.getAttribute('data-lynx-uri');
                if (uri && !uri.includes('facebook.com')) { externalUrl = uri; break; }
                try {
                    const store = JSON.parse(node.getAttribute('data-store') || '{}');
                    if (store.url && !store.url.includes('facebook.com')) { externalUrl = store.url; break; }
                } catch(e) {}
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
    max_cards: int = MAX_CARDS,
) -> list[dict]:
    # accumulate across scrolls — a final snapshot would only see the ~20-30 not-yet-evicted cards
    accumulated: dict[str, dict] = {}
    stable = 0
    last_height = 0
    scroll_times: list[float] = []

    for i in range(max_scrolls):
        t0 = time.perf_counter()
        new_height = await page.evaluate(SCROLL_SCRIPT)
        cards = await page.evaluate(EXTRACT_SCRIPT)
        elapsed = time.perf_counter() - t0
        scroll_times.append(elapsed)

        prev_total = len(accumulated)
        for card in cards:
            _merge_card(accumulated, card)
        current_total = len(accumulated)

        logger.info(
            f"Scroll {i + 1}/{max_scrolls}: height={new_height}, "
            f"dom={len(cards)}, total={current_total}, t={elapsed:.1f}s"
        )

        if current_total >= max_cards:
            logger.warning(
                f"[scroll] MAX_CARDS={max_cards} reached at scroll {i + 1} — stopping"
            )
            break

        # Degrade detector: if scroll time is 3× median of first 5 scrolls → browser is
        # running out of memory, stop now rather than waiting for OOM kill.
        if len(scroll_times) >= 6:
            median_early = sorted(scroll_times[:5])[2]
            if elapsed > median_early * _DEGRADE_FACTOR and elapsed > _DEGRADE_MIN_S:
                logger.warning(
                    f"[scroll] Degrade detected at scroll {i + 1}: "
                    f"t={elapsed:.1f}s vs median={median_early:.1f}s — stopping early"
                )
                break

        # end of feed: no new cards and height stopped growing for N rounds
        if current_total == prev_total and new_height == last_height:
            stable += 1
            if stable >= stable_rounds:
                logger.info(f"Reached end of feed after {i + 1} scrolls")
                break
        else:
            stable = 0

        last_height = new_height
        await asyncio.sleep(1.5)

    # nudge lazy <video> elements into view, then re-extract to backfill their media
    await page.evaluate("""
        () => {
            for (const v of document.querySelectorAll('video')) {
                const src = v.src || v.currentSrc || v.querySelector('source')?.src;
                if (!src) v.scrollIntoView({behavior: 'instant', block: 'center'});
            }
        }
    """)
    await asyncio.sleep(2)
    for card in await page.evaluate(EXTRACT_SCRIPT):
        _merge_card(accumulated, card)

    logger.info(f"Total extracted: {len(accumulated)} cards")
    return list(accumulated.values())
