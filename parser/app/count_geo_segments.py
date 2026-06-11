"""
Count FB Ad Library ads for a country by segmenting on date ranges.

Recursively sub-segments: month → weeks → days until each segment reads < 50,000.
Handles __rd_verify anti-bot challenge with per-segment retries.

Usage:
    python -m app.count_geo_segments --country=AM
    python -m app.count_geo_segments --country=AM --from=2023-01 --to=2025-06
    python -m app.count_geo_segments --country=DE --from=2024-01 --to=2025-06
"""
import asyncio
import argparse
import re
from datetime import date, timedelta
from dataclasses import dataclass, field
from loguru import logger

from app.browser import browser_context
from app.parsers.library_extractor import FB_RESULTS_LABEL_SCRIPT


CAP = 50_000
MAX_RETRIES = 3
RETRY_DELAY = 6        # base seconds; multiplied by attempt number
INTER_REQUEST_DELAY = 2


# ─── URL ──────────────────────────────────────────────────────────────────────

def build_date_url(country: str, d_min: str, d_max: str) -> str:
    return (
        "https://www.facebook.com/ads/library/"
        f"?active_status=all&ad_type=all&country={country}"
        f"&q=%25&search_type=keyword_unordered&media_type=all"
        f"&start_date%5Bmin%5D={d_min}&start_date%5Bmax%5D={d_max}"
    )


# ─── Label parsing ────────────────────────────────────────────────────────────

def parse_label(label: str) -> tuple[int, bool]:
    """Returns (count, is_capped). is_capped means FB hit the '>50,000' ceiling."""
    if not label or label in ("not found", "FAILED"):
        return 0, False
    if re.search(r"no results", label, re.I):
        return 0, False
    is_capped = ">" in label or "+" in label
    m = re.search(r"[\d,]+", label)
    if not m:
        return 0, False
    return int(m.group().replace(",", "")), is_capped


# ─── Period splitting ─────────────────────────────────────────────────────────

def months_range(from_ym: str, to_ym: str) -> list[tuple[str, str]]:
    y, m = int(from_ym[:4]), int(from_ym[5:7])
    ey, em = int(to_ym[:4]), int(to_ym[5:7])
    result = []
    while (y, m) <= (ey, em):
        d_min = date(y, m, 1)
        d_max = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y + 1, 1, 1) - timedelta(days=1)
        result.append((str(d_min), str(d_max)))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return result


def split_period(d_min: str, d_max: str) -> list[tuple[str, str]] | None:
    """
    Split into smaller chunks.
    >7 days → weekly (7-day) chunks.
    2-7 days → individual days.
    1 day → None (cannot split further).
    """
    a = date.fromisoformat(d_min)
    b = date.fromisoformat(d_max)
    total_days = (b - a).days + 1
    if total_days <= 1:
        return None
    chunk = 7 if total_days > 7 else 1
    result = []
    d = a
    while d <= b:
        end = min(d + timedelta(days=chunk - 1), b)
        result.append((str(d), str(end)))
        d = end + timedelta(days=1)
    return result


# ─── Fetch (with retry / challenge detection) ─────────────────────────────────

async def _fetch_once(context, url: str) -> tuple[str, bool]:
    """Returns (label, is_challenge)."""
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        html = await page.content()
        if "__rd_verify" in html:
            return "not found", True
        try:
            await page.wait_for_selector(
                'div:has-text("Library ID"), div:has-text("No results")',
                timeout=15_000,
            )
        except Exception:
            pass
        await asyncio.sleep(3)
        label = await page.evaluate(FB_RESULTS_LABEL_SCRIPT)
        return label, False
    finally:
        await page.close()


async def fetch(context, country: str, d_min: str, d_max: str) -> tuple[str, bool]:
    """
    Returns (label, ok).
    ok=False → all retries exhausted; caller should mark segment FAILED.
    """
    url = build_date_url(country, d_min, d_max)
    for attempt in range(1, MAX_RETRIES + 1):
        if attempt > 1:
            wait = RETRY_DELAY * attempt
            logger.warning(f"    retry {attempt}/{MAX_RETRIES} ({d_min}→{d_max}), waiting {wait}s")
            await asyncio.sleep(wait)
        label, is_challenge = await _fetch_once(context, url)
        if is_challenge:
            logger.warning(f"    __rd_verify on attempt {attempt}")
            continue
        if label == "not found":
            logger.warning(f"    no results label on attempt {attempt}")
            continue
        return label, True
    return "FAILED", False


# ─── Recursive measurement ────────────────────────────────────────────────────

@dataclass
class Seg:
    d_min: str
    d_max: str
    count: int
    label: str
    failed: bool = False
    day_capped: bool = False    # day-level still >50k, can't drill further
    subs: list = field(default_factory=list)


async def measure(context, country: str, d_min: str, d_max: str, depth: int = 0) -> Seg:
    indent = "  " * depth
    label, ok = await fetch(context, country, d_min, d_max)

    if not ok:
        logger.error(f"{indent}❌ FAILED  {d_min} → {d_max}")
        return Seg(d_min=d_min, d_max=d_max, count=0, label="FAILED", failed=True)

    count, is_capped = parse_label(label)
    marker = " ⚠ >50k" if is_capped else ""
    logger.info(f"{indent}{d_min} → {d_max}  {label!r} → {count}{marker}")

    if not is_capped:
        await asyncio.sleep(INTER_REQUEST_DELAY)
        return Seg(d_min=d_min, d_max=d_max, count=count, label=label)

    sub_periods = split_period(d_min, d_max)
    if sub_periods is None:
        logger.warning(f"{indent}⚠ DAY-CAP: {d_min} still >{CAP:,} — cannot split further")
        return Seg(d_min=d_min, d_max=d_max, count=count, label=label, day_capped=True)

    logger.info(f"{indent}splitting into {len(sub_periods)} sub-periods (depth {depth+1})")
    subs: list[Seg] = []
    for sd_min, sd_max in sub_periods:
        subs.append(await measure(context, country, sd_min, sd_max, depth + 1))

    total = sum(s.count for s in subs)
    return Seg(
        d_min=d_min, d_max=d_max,
        count=total,
        label=f"sub-segmented → {total:,}",
        failed=any(s.failed for s in subs),
        day_capped=any(s.day_capped for s in subs),
        subs=subs,
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

async def run(country: str, from_ym: str, to_ym: str) -> None:
    segments = months_range(from_ym, to_ym)
    logger.info(f"[seg] country={country}  {from_ym} → {to_ym}  ({len(segments)} months)")

    results: list[Seg] = []
    async with browser_context() as context:
        for d_min, d_max in segments:
            seg = await measure(context, country, d_min, d_max)
            results.append(seg)

    total = sum(r.count for r in results)
    failed = [r for r in results if r.failed]
    capped = [r for r in results if r.day_capped]

    W = 68
    print("\n" + "=" * W)
    print(f"  COUNTRY: {country}   {from_ym} → {to_ym}")
    print(f"  {'Period':<25}  {'Label / note':<26}  {'Count':>9}")
    print("-" * W)
    for r in results:
        note = ""
        if r.failed:    note = " ❌"
        elif r.day_capped: note = " ⚠"
        print(f"  {r.d_min} – {r.d_max}   {r.label:<26}  {r.count:>9,}{note}")
    print("-" * W)
    print(f"  {'TOTAL':>53}  {total:>9,}")
    if failed:
        print(f"\n  ❌ {len(failed)} segment(s) FAILED — actual total is HIGHER than shown")
    if capped:
        print(f"  ⚠  {len(capped)} day-level cap(s) — those days still show >{CAP:,}")
    if not failed and not capped:
        print(f"\n  ✓ All segments resolved below {CAP:,} — total is exact")
    print("=" * W)


if __name__ == "__main__":
    _today = date.today()
    _two_years_ago = _today - timedelta(days=730)
    _default_from = f"{_two_years_ago.year:04d}-{_two_years_ago.month:02d}"
    _default_to = f"{_today.year:04d}-{_today.month:02d}"

    ap = argparse.ArgumentParser(description="Count FB Ad Library ads by date segments")
    ap.add_argument("--country", required=True, help="ISO country code, e.g. AM, DE, IN")
    ap.add_argument("--from", dest="from_ym", default=_default_from, metavar="YYYY-MM",
                    help=f"start month (default: {_default_from})")
    ap.add_argument("--to", dest="to_ym", default=_default_to, metavar="YYYY-MM",
                    help=f"end month (default: {_default_to})")
    args = ap.parse_args()
    asyncio.run(run(args.country, args.from_ym, args.to_ym))
