import asyncio
import os
import random
import re
from datetime import datetime, timezone
from sqlalchemy import select
from loguru import logger

from app.db import AsyncSessionLocal
from app.models import Ad
from app.browser import browser_context, goto_with_challenge_retry
from app.parsers.library_card import parse_card_text
from app.proxy import rotate_ip

_SPEND_CPM = float(os.getenv("SPEND_CPM", "12"))


BATCH_SIZE = 500   # max active ads per daily run; None = unlimited
GROUP_SIZE = 3     # ads processed in parallel per browser context
GROUP_SLEEP = 10   # seconds between groups (anti-ban pause)

# JS: find the external landing URL on the ?id= page.
# Works for both active (CTA button present) and inactive (URL may be in data attrs).
_URL_SCRIPT = """
() => {
    const SKIP = [
        'facebook.com', 'fb.com', 'fb.me', 'instagram.com', 'meta.com',
        'metastatus.com', 'about.fb.com', 'messenger.com', 'whatsapp.com',
        'oculus.com', 'workplace.com',
    ];
    const blocked = (url) => SKIP.some(d => url.includes(d));

    const decode = (href) => {
        try {
            if (href.includes('l.facebook.com') || href.includes('l.fb.me')) {
                const dest = new URL(href).searchParams.get('u');
                if (dest) return decodeURIComponent(dest);
            }
        } catch(e) {}
        return null;
    };
    for (const a of document.querySelectorAll('a[href]')) {
        const href = a.href || '';
        if (!href || href.startsWith('about:')) continue;
        const dec = decode(href);
        if (dec && !blocked(dec)) return dec;
        if (!blocked(href)) return href;
    }
    for (const node of document.querySelectorAll('[data-lynx-uri]')) {
        const uri = node.getAttribute('data-lynx-uri') || '';
        if (uri && !blocked(uri)) return uri;
    }
    for (const node of document.querySelectorAll('[data-store]')) {
        try {
            const s = JSON.parse(node.getAttribute('data-store') || '{}');
            if (s.url && !blocked(s.url)) return s.url;
        } catch(e) {}
    }
    return null;
}
"""


# Button texts that reveal the EU details panel (multi-language, ported from debug_eu_ads.py).
_EXPAND_TEXTS = [
    "See ad details", "Ad details", "About this ad",
    "Informazioni sull'inserzione", "Información del anuncio",
    "Información sobre el anuncio", "Dati sull'inserzione",
    "Ver detalles del anuncio", "Ver los detalles del anuncio",
    "Dettagli dell'inserzione", "Подробнее", "See details", "Details",
]

# Texts inside the opened panel to expand the Transparency/Reach sub-section.
_TRANSPARENCY_TEXTS = [
    "Transparency by location", "Transparency",
    "Reach by location", "EU transparency", "Ad reach", "Reach",
]

# JS: extract Targeting table and Reach table rows from the already-opened EU block.
# Runs on the live DOM after the two expand clicks — no extra navigation, no memory cost.
_EU_TABLE_SCRIPT = """
() => {
    const result = { targeting: [], demographic: [] };

    // <table aria-label="Targeting table"> — cols: Location / Location type / Included or excluded
    const tgt = document.querySelector('[aria-label="Targeting table"]');
    if (tgt) {
        for (const row of tgt.querySelectorAll('tbody tr')) {
            const cells = [...row.querySelectorAll('td')];
            if (cells.length >= 3) {
                result.targeting.push({
                    location: (cells[0].innerText || '').trim(),
                    type:     (cells[1].innerText || '').trim(),
                    status:   (cells[2].innerText || '').trim()
                });
            }
        }
    }

    // <table aria-label^="Reach table"> — cols: Location / Age Range / Gender / Reach
    const rch = document.querySelector('[aria-label^="Reach table"]');
    if (rch) {
        for (const row of rch.querySelectorAll('tbody tr')) {
            const cells = [...row.querySelectorAll('td')];
            if (cells.length >= 4) {
                const rawNum = (cells[3].innerText || '').replace(/[^0-9]/g, '');
                result.demographic.push({
                    location: (cells[0].innerText || '').trim(),
                    age:      (cells[1].innerText || '').trim(),
                    gender:   (cells[2].innerText || '').trim(),
                    reach:    rawNum ? parseInt(rawNum, 10) : 0
                });
            }
        }
    }

    return result;
}
"""

# JS: find the smallest container that holds the most EU transparency markers.
_EU_MARKERS = [
    "Transparency by location", "EU transparency", "Ad Details",
    "Reach by location", "About the advertiser", "Advertiser and payer",
    "Audience", "Age", "Gender",
]
_EU_MARKERS_JS = str(_EU_MARKERS).replace("'", '"')
_EU_BLOCK_SCRIPT = f"""
() => {{
    const MARKERS = {_EU_MARKERS_JS};
    let best = null, bestScore = Infinity;
    for (const el of document.querySelectorAll('div, section, main, article')) {{
        const text = (el.innerText || '').trim();
        if (text.length < 100 || text.length > 30000) continue;
        const hits = MARKERS.filter(m => text.includes(m)).length;
        if (hits === 0) continue;
        const score = text.length / (hits * hits);
        if (score < bestScore) {{ best = el; bestScore = score; }}
    }}
    if (!best) return null;
    return {{ text: best.innerText, score: Math.round(bestScore), len: best.innerText.length }};
}}
"""


async def _eu_click_text(page, candidates: list[str]) -> bool:
    """Click the first small element matching any candidate text. Returns True if clicked."""
    for text in candidates:
        try:
            loc = page.get_by_text(text, exact=False)
            n = await loc.count()
            if n == 0:
                continue
            for i in range(min(n, 4)):
                el = loc.nth(i)
                inner = (await el.inner_text(timeout=2_000)).strip()
                if len(inner) < 120:
                    await el.click(timeout=5_000)
                    await asyncio.sleep(2)
                    return True
        except Exception:
            pass
    return False


_INCLUDED_WORDS = ("includ", "включ", "incluid", "inclus")
_COUNTRY_WORDS = ("country", "страна", "pais", "país", "land", "pays")


def _is_included_status(status: str) -> bool:
    s = status.lower()
    return any(w in s for w in _INCLUDED_WORDS)


def _is_country_type(type_str: str) -> bool:
    t = type_str.lower()
    return any(w in t for w in _COUNTRY_WORDS)


def _build_breakdown(block_text: str, dom_data: dict | None) -> dict | None:
    """Build the reach_breakdown structure from DOM table data + block text for age/gender."""
    if not dom_data:
        return None

    targeting_rows = dom_data.get("targeting") or []
    demographic_rows = dom_data.get("demographic") or []

    # Age: line after "Age" heading, strip trailing "years old"
    age: str | None = None
    m = re.search(r'\bAge\b\s*\n+\s*([^\n]+)', block_text, re.IGNORECASE)
    if m:
        age_raw = m.group(1).strip()
        age = re.sub(r'\s*years?\s*old.*$', '', age_raw, flags=re.IGNORECASE).strip() or None

    # Gender: line after "Gender" heading
    gender: str | None = None
    m = re.search(r'\bGender\b\s*\n+\s*([^\n]+)', block_text, re.IGNORECASE)
    if m:
        gender = m.group(1).strip() or None

    countries_included = [
        r["location"] for r in targeting_rows
        if _is_country_type(r.get("type", "")) and _is_included_status(r.get("status", ""))
    ]

    has_targeting = bool(targeting_rows or age or gender)
    has_demographic = bool(demographic_rows)

    if not has_targeting and not has_demographic:
        return None

    breakdown: dict = {}
    if has_targeting:
        targeting: dict = {"locations": targeting_rows}
        if countries_included:
            targeting["countries_included"] = countries_included
        if age:
            targeting["age"] = age
        if gender:
            targeting["gender"] = gender
        breakdown["targeting"] = targeting
    if has_demographic:
        breakdown["demographic"] = demographic_rows

    return breakdown


def _parse_eu_reach(text: str, library_id: str, dom_data: dict | None = None) -> dict | None:
    """Parse reach number from EU block innerText; build breakdown from DOM table data."""
    if not text:
        return None

    # Reach: first formatted number after any reach keyword (multi-language)
    reach: int | None = None
    m = re.search(
        r'(?:reach|охват|alcance|portata|portée)[^\d]*(\d[\d,.\s]*)',
        text, re.IGNORECASE,
    )
    if m:
        raw = m.group(1).split('\n')[0][:15]
        num_str = re.sub(r'[\s,.]', '', raw)
        try:
            reach = int(num_str)
        except ValueError:
            pass

    if not reach or reach <= 0:
        return None
    if reach > 100_000_000 or str(reach) == library_id:
        return None

    breakdown = _build_breakdown(text, dom_data)
    if breakdown is None:
        # Regex caught a stray number but no EU tables parsed — not an EU creative
        return None
    return {"reach": reach, "breakdown": breakdown}


def _build_ad_url(library_id: str) -> str:
    return f"https://www.facebook.com/ads/library/?id={library_id}&country=DE"


async def _process_one_page(
    context,
    library_id: str,
) -> tuple[str | bool | None, str | None, dict | None]:
    """Open one page in an existing browser context and check the ad.

    Returns (card_result, found_url, reach_data) where:
      card_result: str = card innerText | False = page OK but no card | None = couldn't verify
      found_url:   str = external landing URL found on page | None = not found
      reach_data:  EU reach dict or None
    """
    url = _build_ad_url(library_id)
    page = await context.new_page()
    try:
        try:
            ok = await goto_with_challenge_retry(page, url, max_attempts=4, base_wait=6)
        except Exception as e:
            logger.warning(f"[refresh] {library_id}: network error {e}")
            return None, None, None

        if not ok:
            logger.warning(f"[refresh] {library_id}: challenge not cleared after retries → skip")
            return None, None, None

        await asyncio.sleep(2)

        try:
            # Reduced from 10 000 — page already loaded after domcontentloaded+sleep
            await page.wait_for_load_state("networkidle", timeout=4_000)
        except Exception:
            pass

        _card_script = f"""
            () => {{
                const needle = 'Library ID: {library_id}';
                for (const el of document.querySelectorAll('div')) {{
                    const t = el.innerText || '';
                    if (t.includes(needle) && t.length > 100 && t.length < 10000) {{
                        return {{ text: t, bodyLen: document.body.innerText.length }};
                    }}
                }}
                return {{ text: null, bodyLen: document.body.innerText.length }};
            }}
        """
        try:
            # Reduced from 20s — JS is synchronous DOM traversal, 10s is plenty
            card_result = await asyncio.wait_for(page.evaluate(_card_script), timeout=10)
        except asyncio.TimeoutError:
            logger.warning(f"[refresh] {library_id}: evaluate timeout → skip")
            return None, None, None
        except Exception as e:
            logger.warning(f"[refresh] {library_id}: evaluate error {e}")
            return None, None, None

        # Extract landing URL in the same page visit
        found_url: str | None = None
        try:
            found_url = await asyncio.wait_for(page.evaluate(_URL_SCRIPT), timeout=10)
        except Exception:
            pass

        # Two-step EU reach extraction: Step 1 = "See ad details", Step 2 = "Transparency by location"
        reach_data: dict | None = None
        try:
            step1 = await _eu_click_text(page, _EXPAND_TEXTS)
            if step1:
                await _eu_click_text(page, _TRANSPARENCY_TEXTS)
            block = await asyncio.wait_for(page.evaluate(_EU_BLOCK_SCRIPT), timeout=10)
            if block and block.get("text"):
                dom_data: dict | None = None
                try:
                    dom_data = await asyncio.wait_for(page.evaluate(_EU_TABLE_SCRIPT), timeout=10)
                except Exception as te:
                    logger.debug(f"[refresh] {library_id}: EU table extract error: {te}")
                reach_data = _parse_eu_reach(block["text"], library_id, dom_data)
                if reach_data:
                    logger.debug(
                        f"[refresh] {library_id}: EU block score={block.get('score')} "
                        f"len={block.get('len')} reach={reach_data.get('reach')}"
                    )
        except Exception as e:
            logger.debug(f"[refresh] {library_id}: EU reach extraction error: {e}")

        if card_result.get("text"):
            return card_result["text"], found_url, reach_data

        if card_result.get("bodyLen", 9999) < 500:
            logger.warning(f"[refresh] {library_id}: page nearly empty → skip")
            return None, None, None

        return False, found_url, reach_data
    finally:
        await page.close()


async def _check_ad_in_context(
    context,
    library_id: str,
) -> tuple[bool | None, str | None, dict | None]:
    """Check one ad using an existing browser context (opens/closes its own tab).

    Returns (is_active, found_url, reach_data):
      is_active:  True = active | False = genuinely gone | None = couldn't verify
    """
    card_text, found_url, reach_data = await _process_one_page(context, library_id)

    if card_text is None:
        return None, None, None

    if card_text is not False:
        try:
            card = parse_card_text(card_text)
            return bool(card.is_active), found_url, reach_data
        except Exception as e:
            logger.warning(f"[refresh] {library_id}: parse error {e}")
            return None, found_url, reach_data

    # Card not found on first attempt — retry once to rule out transient proxy disruption.
    # asyncio.sleep here is non-blocking: the other coroutines in the group already finished.
    logger.warning(f"[refresh] {library_id}: card not found on first attempt, retrying in 15s")
    await asyncio.sleep(15)

    card_text2, found_url2, reach_data2 = await _process_one_page(context, library_id)

    if card_text2 is None:
        return None, None, None

    if card_text2 is not False:
        try:
            card = parse_card_text(card_text2)
            return bool(card.is_active), found_url2 or found_url, reach_data2 or reach_data
        except Exception as e:
            logger.warning(f"[refresh] {library_id}: parse error on retry {e}")
            return None, found_url2 or found_url, reach_data2 or reach_data

    logger.info(f"[refresh] {library_id}: card not found after retry → INACTIVE")
    return False, found_url2 or found_url, reach_data2 or reach_data


async def _process_group(
    ads: list[Ad],
) -> list[tuple[Ad, bool | None, str | None, dict | None, bool]]:
    """Open one browser context and check GROUP_SIZE ads in parallel (one tab each).

    Returns list of (ad, is_active, found_url, reach_data, is_error).
    """
    async with browser_context() as context:
        raw = await asyncio.gather(
            *[_check_ad_in_context(context, ad.library_id) for ad in ads],
            return_exceptions=True,
        )

    out = []
    for ad, r in zip(ads, raw):
        if isinstance(r, Exception):
            logger.error(f"[refresh] {ad.library_id}: group task error: {r}")
            out.append((ad, None, None, None, True))
        else:
            out.append((ad, *r, False))
    return out


async def _get_active_ads(session, limit: int | None) -> list[Ad]:
    stmt = (
        select(Ad)
        .where(Ad.is_active.is_(True))
        .order_by(Ad.last_refresh_at.nulls_first(), Ad.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())


def _compute_days_active(ad: Ad, now: datetime) -> int:
    ref = ad.started_at or ad.first_seen_at
    if ref:
        ref = ref.replace(tzinfo=timezone.utc) if ref.tzinfo is None else ref
        return max(0, (now - ref).days)
    return ad.days_active


async def refresh_batch(limit: int | None = BATCH_SIZE) -> dict:
    now = datetime.now(timezone.utc)
    stats = {
        "checked": 0, "deactivated": 0, "still_active": 0,
        "unknown": 0, "errors": 0, "url_enriched": 0, "reach_updated": 0,
    }

    async with AsyncSessionLocal() as session:
        ads = await _get_active_ads(session, limit)

    groups = [ads[i:i + GROUP_SIZE] for i in range(0, len(ads), GROUP_SIZE)]
    logger.info(
        f"[refresh] Starting batch of {len(ads)} active ads "
        f"({len(groups)} groups × {GROUP_SIZE})"
    )

    for g_idx, group in enumerate(groups, 1):
        ids = [a.library_id for a in group]
        logger.info(f"[refresh] Group [{g_idx}/{len(groups)}]: {ids}")

        try:
            results = await _process_group(group)
        except Exception as e:
            logger.error(f"[refresh] Group {g_idx} fatal: {e}")
            results = [(ad, None, None, None, True) for ad in group]

        for ad, is_active, found_url, reach_data, is_error in results:
            stats["checked"] += 1
            if is_error:
                stats["errors"] += 1

            try:
                async with AsyncSessionLocal() as session:
                    db_ad = await session.get(Ad, ad.id)
                    if not db_ad:
                        continue

                    db_ad.last_refresh_at = now

                    if is_active is None:
                        stats["unknown"] += 1
                    elif is_active:
                        db_ad.last_seen_at = now
                        db_ad.days_active = _compute_days_active(db_ad, now)
                        stats["still_active"] += 1
                    else:
                        db_ad.is_active = False
                        db_ad.days_active = _compute_days_active(db_ad, now)
                        stats["deactivated"] += 1
                        logger.info(f"[refresh] {ad.library_id}: marked INACTIVE")

                    if found_url and not db_ad.link_url:
                        db_ad.link_url = found_url
                        stats["url_enriched"] += 1
                        logger.info(f"[refresh] {ad.library_id}: link_url enriched → {found_url}")

                    if reach_data and reach_data.get("reach"):
                        reach_val = reach_data["reach"]
                        if reach_val > 100_000_000 or str(reach_val) == ad.library_id:
                            logger.warning(f"[refresh] {ad.library_id}: suspicious reach={reach_val} — skipped")
                        else:
                            db_ad.reach = reach_val
                            breakdown = reach_data.get("breakdown")
                            db_ad.reach_breakdown = breakdown
                            targeting = (breakdown or {}).get("targeting") or {}
                            db_ad.eu_countries = targeting.get("countries_included") or None
                            db_ad.spend_estimate = int(reach_val * _SPEND_CPM / 1000)
                            stats["reach_updated"] += 1
                            logger.info(f"[refresh] {ad.library_id}: reach={reach_val}")

                    await session.commit()
            except Exception as e:
                logger.error(f"[refresh] {ad.library_id}: DB write error: {e}")
                stats["errors"] += 1

        if g_idx < len(groups):
            # Rotate IP roughly every 5 groups (~15 ads, similar cadence as before)
            if g_idx % 5 == 0:
                logger.info("[refresh] Rotating IP")
                await rotate_ip()
            logger.debug(f"[refresh] group sleep {GROUP_SLEEP}s")
            await asyncio.sleep(GROUP_SLEEP)

    logger.info(f"[refresh] Done: {stats}")
    return stats


async def run_refresh_once():
    logger.info(f"[refresh] Daily run started, batch_limit={BATCH_SIZE}")
    try:
        stats = await refresh_batch(limit=None)
        logger.info(f"[refresh] Daily run complete: {stats}")
    except Exception as e:
        logger.error(f"[refresh] Daily run error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(run_refresh_once())
