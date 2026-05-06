"""Tests for the h5ai API client using respx to mock HTTP."""
import pytest
import respx
import httpx

from elscione_dl.api import Entry, list_dir, _make_client


BASE = "https://server.elscione.com"

_MOCK_RESPONSE = {
    "items": [
        {"href": "/Novels/", "time": 1000, "size": None, "managed": True, "fetched": True},
        {"href": "/Novels/Title One/", "time": 2000, "size": None, "managed": True, "fetched": False},
        {"href": "/Novels/Title Two/", "time": 3000, "size": None, "managed": True, "fetched": False},
        {"href": "/Novels/Title One/volume1.epub", "time": 4000, "size": 1024, "managed": True, "fetched": False},
        # Sibling at parent level — should be excluded
        {"href": "/Other/", "time": 5000, "size": None, "managed": True, "fetched": False},
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_list_dir_returns_direct_children():
    respx.post(f"{BASE}/_h5ai/public/index.php").mock(
        return_value=httpx.Response(200, json=_MOCK_RESPONSE)
    )
    async with _make_client() as client:
        children = await list_dir(client, "/Novels/")

    # Should only include direct children of /Novels/
    hrefs = {e.href for e in children}
    assert "/Novels/Title One/" in hrefs
    assert "/Novels/Title Two/" in hrefs
    # Should NOT include parent or sibling
    assert "/Novels/" not in hrefs
    assert "/Other/" not in hrefs


@pytest.mark.asyncio
@respx.mock
async def test_list_dir_identifies_dirs_and_files():
    respx.post(f"{BASE}/_h5ai/public/index.php").mock(
        return_value=httpx.Response(200, json=_MOCK_RESPONSE)
    )
    async with _make_client() as client:
        children = await list_dir(client, "/Novels/")

    dirs = [e for e in children if e.is_dir]
    files = [e for e in children if not e.is_dir]
    # Title dirs should be detected as dirs, files as files
    assert any(e.href == "/Novels/Title One/" for e in dirs)


def test_entry_name():
    e = Entry(href="/Novels/My%20Title/vol1.epub", is_dir=False, size=100, mtime=0)
    assert e.name == "vol1.epub"


def test_entry_name_dir():
    e = Entry(href="/Novels/My%20Title/", is_dir=True, size=None, mtime=0)
    assert e.name == "My Title"


def test_entry_ext():
    e = Entry(href="/foo/bar.epub", is_dir=False, size=10, mtime=0)
    assert e.ext == ".epub"


def test_entry_ext_pdf():
    e = Entry(href="/foo/bar.pdf", is_dir=False, size=10, mtime=0)
    assert e.ext == ".pdf"


def test_entry_url():
    e = Entry(href="/Novels/bar.epub", is_dir=False, size=10, mtime=0)
    assert e.url == f"{BASE}/Novels/bar.epub"


# ---------- filter_latest_versions ----------
from elscione_dl.api import filter_latest_versions, _file_dedup_key


def _f(name: str) -> Entry:
    return Entry(href=f"/Novels/Title/{name}", is_dir=False, size=10, mtime=0)


def test_dedup_key_no_version():
    key, ver = _file_dedup_key("Volume 01 [Seven Seas][Kobo].epub")
    assert ver == 1
    assert "v1" not in key
    assert "v2" not in key


def test_dedup_key_with_version():
    key, ver = _file_dedup_key("Volume 01 [Seven Seas]{v2}[Kobo].epub")
    assert ver == 2
    # The base key should match the no-version version (case-insensitive)
    base_key, _ = _file_dedup_key("Volume 01 [Seven Seas][Kobo].epub")
    assert key == base_key


def test_filter_latest_keeps_highest_version():
    entries = [
        _f("Volume 01 [Seven Seas][Kobo].epub"),
        _f("Volume 01 [Seven Seas]{v2}[Kobo].epub"),
        _f("Volume 02 [Seven Seas][Kobo].epub"),
    ]
    result = filter_latest_versions(entries)
    names = sorted(e.name for e in result)
    assert names == [
        "Volume 01 [Seven Seas]{v2}[Kobo].epub",
        "Volume 02 [Seven Seas][Kobo].epub",
    ]


def test_filter_latest_keeps_distinct_extensions():
    # Same volume, different formats should both be kept
    entries = [
        _f("Volume 01.epub"),
        _f("Volume 01.pdf"),
    ]
    result = filter_latest_versions(entries)
    assert len(result) == 2


def test_filter_latest_v3_beats_v2():
    entries = [
        _f("Vol 01 {v2}.epub"),
        _f("Vol 01 {v3}.epub"),
        _f("Vol 01.epub"),
    ]
    result = filter_latest_versions(entries)
    assert len(result) == 1
    assert "{v3}" in result[0].name
