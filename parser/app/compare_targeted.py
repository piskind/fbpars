"""
Compare FB Ad Library result counts for is_targeted_country=true/false/absent.

Hypothesis: true = only ads targeted at this country (real local volume),
            false / absent = all ads ever shown (includes global cross-geo flood).

Reads only the FB results label — no scrolling.

Usage:
    python -m app.compare_targeted
    python -m app.compare_targeted --countries=AM,AZ,DE
"""
import asyncio
import argparse
import re
from loguru import logger

from app.browser import browser_context, build_library_url_country_only
from app.parsers.library_extractor import FB_RESULTS_LABEL_SCRIPT

MAX_RETRIES = 3
RETRY_DELAY = 6


# Braille blank U+2800 — alternative wildcard used by some researchers
Q_BRAILLE = "%E2%A0%80"
Q_PCT     = "%25"


def _variants(country: str) -> list[tuple[str, str]]:
    """Returns list of (label, url) for all variants to test."""
    return [
        ("q=%25  targeted=true",  build_library_url_country_only(country, is_targeted_country=True,  q=Q_PCT)),
        ("q=%25  targeted=false", build_library_url_country_only(country, is_targeted_country=False, q=Q_PCT)),
        ("q=%25  no param",       build_library_url_country_only(country, is_targeted_country=None,  q=Q_PCT)),
        ("q=U+2800 targeted=true", build_library_url_country_only(country, is_targeted_country=True,  q=Q_BRAILLE)),
        ("q=U+2800 no param",      build_library_url_country_only(country, is_targeted_country=None,  q=Q_BRAILLE)),
    ]


async def _read_label_once(page, url: str) -> tuple[str, bool]:
    """Navigate to url, read FB results label. Returns (label, is_challenge)."""
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    html = await page.content()
    if "__rd_verify" in html:
        return "challenge", True

    try:
        await page.wait_for_selector(
            'div:has-text("Library ID"), div:has-text("No results")',
            timeout=15_000,
        )
    except Exception:
        pass

    try:
        await page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass

    try:
        html2 = await page.content()
        if "__rd_verify" in html2:
            return "challenge", True
    except Exception:
        return "challenge", True

    await asyncio.sleep(1)

    try:
        label = await page.evaluate(FB_RESULTS_LABEL_SCRIPT)
    except Exception as e:
        msg = str(e).lower()
        if "context" in msg or "destroyed" in msg or "navigation" in msg:
            return "challenge", True
        return f"error: {e}", False

    return label, False


async def _read_label(context, url: str) -> str:
    """Read label with retries. Returns final label string."""
    for attempt in range(1, MAX_RETRIES + 1):
        if attempt > 1:
            wait = RETRY_DELAY * attempt
            logger.warning(f"    retry {attempt}/{MAX_RETRIES}, waiting {wait}s")
            await asyncio.sleep(wait)

        page = await context.new_page()
        try:
            label, is_challenge = await _read_label_once(page, url)
        finally:
            await page.close()

        if is_challenge:
            logger.warning(f"    challenge on attempt {attempt}")
            continue

        return label

    return "FAILED (challenge)"


def _parse_count(label: str) -> str:
    """Extract count from label for display."""
    if not label or label in ("not found", "FAILED (challenge)"):
        return label
    if re.search(r"no results", label, re.I):
        return "0"
    m = re.search(r"[\d,]+", label)
    return m.group() if m else label


async def run(countries: list[str]) -> None:
    rows: list[tuple[str, str, str, str]] = []  # country, variant, raw_label, count

    async with browser_context() as context:
        for country in countries:
            logger.info(f"\n── {country} ────────────────────────")
            for variant_label, url in _variants(country):
                logger.info(f"  {variant_label}")
                logger.info(f"  → {url}")
                label = await _read_label(context, url)
                count = _parse_count(label)
                logger.info(f"  ← {label!r}  →  {count}")
                rows.append((country, variant_label, label, count))
                await asyncio.sleep(2)

    # Print comparison table
    W = 72
    print("\n" + "=" * W)
    print(f"  {'Country':<6}  {'Variant':<28}  {'Raw label':<22}  {'Count':>10}")
    print("-" * W)
    prev_country = None
    for country, variant, raw, count in rows:
        if prev_country and country != prev_country:
            print()
        print(f"  {country:<6}  {variant:<28}  {raw:<22}  {count:>10}")
        prev_country = country
    print("=" * W)
    print()

    # Verdict
    by_country: dict[str, dict[str, str]] = {}
    for country, variant, _, count in rows:
        by_country.setdefault(country, {})[variant] = count

    for country, variants in by_country.items():
        t = variants.get("q=%25  targeted=true",  "?")
        f = variants.get("q=%25  targeted=false", "?")
        n = variants.get("q=%25  no param",       "?")
        print(f"  {country}: true={t}  false={f}  no_param={n}")

    print()
    print("  Hypothesis: true << false ≈ no_param → targeted=true gives real local volume")
    print("=" * W)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--countries", default="AM,AZ", help="Comma-separated ISO codes")
    args = ap.parse_args()
    countries = [c.strip().upper() for c in args.countries.split(",") if c.strip()]
    asyncio.run(run(countries))
