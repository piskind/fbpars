import asyncio
import json
from pathlib import Path
from loguru import logger
from app.browser import browser_context, build_library_url


async def main():
    keyword = "oxys"
    country = "PE"
    url = build_library_url(country, keyword)
    logger.info(f"Opening: {url}")

    async with browser_context() as context:
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        await page.wait_for_selector('div:has-text("Library ID")', timeout=30_000)
        await asyncio.sleep(5)

        logger.info("Looking for card containers")

        candidates = await page.evaluate("""
            () => {
                const results = [];
                const all = document.querySelectorAll('div');
                for (const el of all) {
                    const txt = el.innerText || '';
                    if (txt.startsWith('Library ID:') && txt.length < 3000) {
                        let parent = el;
                        for (let i = 0; i < 8; i++) {
                            if (!parent.parentElement) break;
                            parent = parent.parentElement;
                            const ptxt = parent.innerText || '';
                            if (ptxt.length > 200 && ptxt.length < 5000 && ptxt.includes('Library ID:')) {
                                results.push({
                                    depth: i,
                                    tag: parent.tagName,
                                    classes: parent.className?.toString().slice(0, 200),
                                    textLen: ptxt.length,
                                    preview: ptxt.slice(0, 300)
                                });
                                break;
                            }
                        }
                        if (results.length >= 3) break;
                    }
                }
                return results;
            }
        """)

        logger.info(f"Found {len(candidates)} card candidates")
        for i, c in enumerate(candidates):
            logger.info(f"--- Candidate {i} ---")
            logger.info(f"  Tag: {c['tag']}")
            logger.info(f"  Depth from Library ID text: {c['depth']}")
            logger.info(f"  Classes: {c['classes']}")
            logger.info(f"  Text length: {c['textLen']}")
            logger.info(f"  Preview:\n{c['preview']}")

        logger.info("Extracting first card HTML")

        first_card_html = await page.evaluate("""
            () => {
                const all = document.querySelectorAll('div');
                for (const el of all) {
                    const txt = el.innerText || '';
                    if (txt.startsWith('Library ID:') && txt.length > 300 && txt.length < 5000) {
                        let parent = el;
                        for (let i = 0; i < 5; i++) {
                            parent = parent.parentElement;
                            if (!parent) break;
                        }
                        return parent?.outerHTML?.slice(0, 50000);
                    }
                }
                return null;
            }
        """)

        if first_card_html:
            out_path = "/tmp/first_card.html"
            Path(out_path).write_text(first_card_html, encoding="utf-8")
            logger.info(f"First card HTML saved: {out_path} ({len(first_card_html)} chars)")

        logger.info("Looking for images")

        images = await page.evaluate("""
            () => {
                const imgs = document.querySelectorAll('img');
                return Array.from(imgs).slice(0, 20).map(img => ({
                    src: img.src?.slice(0, 200),
                    alt: img.alt,
                    width: img.naturalWidth,
                    height: img.naturalHeight
                })).filter(i => i.src?.startsWith('http'));
            }
        """)
        logger.info(f"Found {len(images)} images")
        for i, img in enumerate(images[:10]):
            logger.info(f"  img[{i}]: {img['width']}x{img['height']} {img['src']}")

        logger.info("Looking for video sources")

        videos = await page.evaluate("""
            () => {
                const vids = document.querySelectorAll('video');
                return Array.from(vids).map(v => ({
                    src: v.src || v.currentSrc,
                    poster: v.poster
                }));
            }
        """)
        logger.info(f"Found {len(videos)} videos")
        for i, v in enumerate(videos):
            logger.info(f"  vid[{i}]: src={v['src'][:100] if v['src'] else None}")
            logger.info(f"  vid[{i}]: poster={v['poster'][:100] if v['poster'] else None}")


if __name__ == "__main__":
    asyncio.run(main())