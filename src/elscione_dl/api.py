"""h5ai directory listing client."""
from __future__ import annotations

import asyncio
import random
import urllib.parse
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .config import get_settings

_RETRYABLE = (httpx.HTTPError, httpx.RemoteProtocolError, httpx.TimeoutException)
_FILE_EXTS = {".epub", ".pdf", ".cbz", ".cbr", ".zip"}


@dataclass
class Entry:
    href: str          # absolute path on server, e.g. /Foo/Bar/file.epub
    is_dir: bool
    size: int | None   # bytes, None for directories
    mtime: int | None  # unix ms

    @property
    def name(self) -> str:
        return urllib.parse.unquote(self.href.rstrip("/").rsplit("/", 1)[-1])

    @property
    def ext(self) -> str:
        return ("." + self.name.rsplit(".", 1)[-1].lower()) if "." in self.name else ""

    @property
    def url(self) -> str:
        cfg = get_settings()
        return cfg.base_url + self.href


def _make_client() -> httpx.AsyncClient:
    cfg = get_settings()
    return httpx.AsyncClient(
        base_url=cfg.base_url,
        http2=True,
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(max_connections=6, max_keepalive_connections=4),
        headers={
            "User-Agent": "elscione-dl/0.1 (+local)",
            "Accept": "application/json, text/html, */*",
        },
        follow_redirects=True,
    )


def _parse_items(data: dict) -> list[Entry]:
    entries: list[Entry] = []
    for item in data.get("items", []):
        href: str = item["href"]
        is_dir = href.endswith("/")
        entries.append(Entry(
            href=href,
            is_dir=is_dir,
            size=item.get("size"),
            mtime=item.get("time"),
        ))
    return entries


@retry(
    retry=retry_if_exception_type(_RETRYABLE),
    stop=stop_after_attempt(5),
    wait=wait_exponential_jitter(initial=1, max=30),
    reraise=True,
)
async def _post_listing(client: httpx.AsyncClient, href: str) -> list[Entry]:
    cfg = get_settings()
    # Jittered throttle between listing requests
    delay = random.uniform(cfg.delay_min_ms, cfg.delay_max_ms) / 1000
    await asyncio.sleep(delay)

    resp = await client.post(
        "/_h5ai/public/index.php",
        data={"action": "get", "items[href]": href, "items[what]": "1"},
    )
    resp.raise_for_status()
    return _parse_items(resp.json())


async def list_dir(client: httpx.AsyncClient, href: str) -> list[Entry]:
    """Return the direct children of the directory at *href*.

    h5ai returns a flat snapshot of its whole cache tree with percent-encoded hrefs.
    We normalise both sides (URL-decode) before comparing so literal-space inputs work.
    An empty result is valid; the caller must not fall back to broader logic.
    """
    all_entries = await _post_listing(client, href)
    # Normalise to decoded form for comparison
    href_clean = urllib.parse.unquote(href.rstrip("/")) + "/"
    children: list[Entry] = []
    for e in all_entries:
        decoded = urllib.parse.unquote(e.href.rstrip("/"))
        parent = decoded.rsplit("/", 1)[0] + "/"
        if parent == href_clean and urllib.parse.unquote(e.href) != href_clean:
            children.append(e)
    return children


async def walk_title(
    client: httpx.AsyncClient,
    title_href: str,
    formats: set[str] | None = None,
    max_depth: int = 3,
) -> AsyncIterator[Entry]:
    """Yield all file entries under *title_href* matching *formats* (e.g. {'.epub', '.pdf'})."""
    async def _recurse(href: str, depth: int) -> AsyncIterator[Entry]:
        if depth > max_depth:
            return
        children = await list_dir(client, href)
        for entry in children:
            if entry.is_dir:
                async for e in _recurse(entry.href, depth + 1):
                    yield e
            else:
                if formats is None or entry.ext in formats:
                    yield entry

    async for e in _recurse(title_href, 1):
        yield e


async def probe_title_formats(
    client: httpx.AsyncClient, title_href: str
) -> set[str]:
    """Return the set of file extensions present in a title directory."""
    exts: set[str] = set()
    async for e in walk_title(client, title_href):
        if e.ext in _FILE_EXTS:
            exts.add(e.ext)
    return exts
