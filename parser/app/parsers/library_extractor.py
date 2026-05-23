import asyncio
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
