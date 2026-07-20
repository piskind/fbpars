"""READ-ONLY diagnostic for the "100% skipped_no_media" symptom.

Writes NOTHING. Fetches a couple of live pagination pages for a config, and for each raw
GraphQL node prints where its media is (or isn't) — the snapshot keys, cards[] shape, and the
result of map_graphql_card — so we can see whether FB stopped returning media for these nodes
or the mapper stopped finding it.

    docker compose run --rm parser python -m app.debug_media --config-id 4
    docker compose run --rm parser python -m app.debug_media --config-id 4 --pages 2 --per-page 8
    docker compose run --rm parser python -m app.debug_media --library-id 1234567890 --config-id 4

Prod uses PAGINATION_MODE=auto (curl first); this mirrors the curl path.
"""
import argparse
import asyncio
import json
import uuid

from loguru import logger

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import ParsingConfig
from app.worker import _build_url_for_config
from app.browser import capture_session_tokens
from app import proxy as proxy_mod
from app.graphql_client import (
    _build_form_data, _parse_response_json, _extract_ads_and_cursor, map_graphql_card,
)
from app.graphql_paginator import _build_variables, _curl_fetch, _CurlAsyncSession, _CURL_AVAILABLE


def _dump_node(node: dict, focus_library_id: str | None) -> bool:
    """Print one node's media structure. Returns True if it had ZERO extracted media."""
    lib = str(node.get("ad_archive_id") or node.get("id") or "")
    if focus_library_id and lib != focus_library_id:
        return False
    snap = node.get("snapshot") or {}
    cards = snap.get("cards") or []
    card = map_graphql_card(node)
    no_media = not (card.image_urls or card.video_urls or card.poster_urls)

    print(f"\n── library_id={lib}  is_active={node.get('is_active')}  display_format={snap.get('display_format')}")
    print(f"   node top-level keys : {sorted(node.keys())}")
    print(f"   has snapshot        : {bool(snap)}  snapshot keys: {sorted(snap.keys())[:25]}")
    print(f"   cards={len(cards)}  images={len(snap.get('images') or [])}  videos={len(snap.get('videos') or [])}")
    if cards:
        print(f"   cards[0] keys       : {sorted(cards[0].keys())}")
        c0 = cards[0]
        print(f"   cards[0] media fields: "
              f"orig_img={bool(c0.get('original_image_url'))} resized_img={bool(c0.get('resized_image_url'))} "
              f"vid_hd={bool(c0.get('video_hd_url'))} vid_sd={bool(c0.get('video_sd_url'))} "
              f"poster={bool(c0.get('video_preview_image_url'))}")
    print(f"   EXTRACTED           : img={len(card.image_urls)} vid={len(card.video_urls)} poster={len(card.poster_urls)}"
          f"  → {'NO MEDIA (would be skipped)' if no_media else 'ok'}")
    if no_media:
        # The whole raw node, so the true structure is visible for a definitive fix.
        print("   RAW NODE (no media found):")
        print("   " + json.dumps(node, ensure_ascii=False)[:4000])
    return no_media


async def main(config_id: int, pages: int, per_page: int, library_id: str | None) -> None:
    async with AsyncSessionLocal() as s:
        cfg = await s.get(ParsingConfig, config_id)
    if cfg is None:
        print(f"config #{config_id} not found")
        return
    url = _build_url_for_config(cfg, cfg.date_from, cfg.date_to)
    print(f"config #{config_id}: {cfg.keyword or '(no kw)'}/{cfg.country} type={cfg.config_type} "
          f"dates={cfg.date_from}..{cfg.date_to}\nURL: {url}")

    if not _CURL_AVAILABLE:
        print("curl_cffi not available in this image — cannot run the curl diagnostic")
        return

    tokens = await capture_session_tokens(url)
    session_id = str(uuid.uuid4())
    cursor = None
    total_nodes = total_no_media = 0

    async with _CurlAsyncSession() as session:
        for p in range(1, pages + 1):
            await proxy_mod.rotate_before_request()
            proxy_url = await proxy_mod.get_proxy_url()
            variables = _build_variables(tokens.variables_template, cursor, session_id)
            form_data = _build_form_data(tokens.as_tokens_dict(), variables)
            status, text = await _curl_fetch(session, tokens, form_data, proxy_url)
            if status != 200 or "1675004" in text:
                print(f"page {p}: HTTP {status} / rate-limited — stopping (try again or use browser mode)")
                break
            nodes, cursor, has_next = _extract_ads_and_cursor(_parse_response_json(text))
            print(f"\n===== page {p}: {len(nodes)} nodes, has_next={has_next} =====")
            for node in nodes[:per_page]:
                total_nodes += 1
                if _dump_node(node, library_id):
                    total_no_media += 1
            if not has_next or not cursor:
                break

    print(f"\n===== SUMMARY: {total_no_media}/{total_nodes} inspected nodes had NO extractable media =====")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read-only: why are cards dropped as skipped_no_media?")
    ap.add_argument("--config-id", type=int, required=True)
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--per-page", type=int, default=6)
    ap.add_argument("--library-id", type=str, default=None, help="only dump this library_id")
    args = ap.parse_args()
    logger.remove()  # keep stdout clean for the dump
    asyncio.run(main(args.config_id, args.pages, args.per_page, args.library_id))
