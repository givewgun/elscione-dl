"""Top-level catalog: enumerate categories and titles from the server."""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from .api import Entry, list_dir, _make_client
from .config import get_settings

_CACHE_DIR = Path.home() / ".cache" / "elscione_dl"
_CATALOG_CACHE = _CACHE_DIR / "catalog.json"
_CACHE_TTL = 3600  # seconds


def _cache_valid() -> bool:
    if not _CATALOG_CACHE.exists():
        return False
    return time.time() - _CATALOG_CACHE.stat().st_mtime < _CACHE_TTL


def _load_cache() -> dict:
    with open(_CATALOG_CACHE) as f:
        return json.load(f)


def _save_cache(data: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_CATALOG_CACHE, "w") as f:
        json.dump(data, f)


async def fetch_categories(client: httpx.AsyncClient) -> list[Entry]:
    """Return top-level category directories (e.g. /Officially Translated Light Novels/)."""
    entries = await list_dir(client, "/")
    return [e for e in entries if e.is_dir]


async def fetch_titles(client: httpx.AsyncClient, category_href: str) -> list[Entry]:
    """Return title directories within a category."""
    entries = await list_dir(client, category_href)
    return [e for e in entries if e.is_dir]


async def fetch_catalog(
    client: httpx.AsyncClient | None = None,
    force_refresh: bool = False,
) -> dict[str, list[Entry]]:
    """
    Return {category_href: [title_entry, ...]} for all categories.
    Uses a file cache with TTL of 1 hour.
    """
    if not force_refresh and _cache_valid():
        raw = _load_cache()
        result: dict[str, list[Entry]] = {}
        for cat_href, titles in raw.items():
            result[cat_href] = [
                Entry(href=t["href"], is_dir=True, size=None, mtime=t.get("mtime"))
                for t in titles
            ]
        return result

    own_client = client is None
    if own_client:
        client = _make_client()

    try:
        categories = await fetch_categories(client)
        result = {}
        cache_raw: dict[str, list[dict]] = {}
        for cat in categories:
            titles = await fetch_titles(client, cat.href)
            result[cat.href] = titles
            cache_raw[cat.href] = [{"href": t.href, "mtime": t.mtime} for t in titles]
        _save_cache(cache_raw)
        return result
    finally:
        if own_client:
            await client.aclose()
