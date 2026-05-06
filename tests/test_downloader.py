"""Tests for resume logic and retry behaviour."""
import asyncio
from pathlib import Path

import pytest
import respx
import httpx

from elscione_dl.api import Entry
from elscione_dl.downloader import _download_one
from elscione_dl.manifest import RunManifest, TitleManifest

BASE = "https://server.elscione.com"


def _make_entry(href: str = "/Novels/Title/vol1.epub", size: int = 100) -> Entry:
    return Entry(href=href, is_dir=False, size=size, mtime=0)


@pytest.mark.asyncio
@respx.mock
async def test_download_creates_file(tmp_path: Path):
    entry = _make_entry()
    content = b"A" * 100
    respx.get(f"{BASE}/Novels/Title/vol1.epub").mock(
        return_value=httpx.Response(
            200,
            content=content,
            headers={"Content-Length": str(len(content))},
        )
    )

    from rich.progress import Progress
    prog = Progress()
    task = prog.add_task("test", total=None)

    async with httpx.AsyncClient(base_url=BASE) as client:
        fname, sz = await _download_one(client, entry, tmp_path, prog, task)

    assert fname == "vol1.epub"
    assert sz == 100
    assert (tmp_path / "vol1.epub").read_bytes() == content


@pytest.mark.asyncio
@respx.mock
async def test_download_resumes_partial(tmp_path: Path):
    entry = _make_entry()
    full_content = b"A" * 50 + b"B" * 50
    # Pre-create a partial file
    part = tmp_path / "vol1.epub.part"
    part.write_bytes(b"A" * 50)

    respx.get(f"{BASE}/Novels/Title/vol1.epub").mock(
        return_value=httpx.Response(
            206,
            content=b"B" * 50,
            headers={"Content-Length": "50", "Accept-Ranges": "bytes"},
        )
    )

    from rich.progress import Progress
    prog = Progress()
    task = prog.add_task("test", total=None)

    async with httpx.AsyncClient(base_url=BASE) as client:
        fname, sz = await _download_one(client, entry, tmp_path, prog, task)

    assert (tmp_path / "vol1.epub").read_bytes() == full_content


@pytest.mark.asyncio
async def test_dry_run_no_file(tmp_path: Path):
    entry = _make_entry()
    from rich.progress import Progress
    prog = Progress()
    task = prog.add_task("test", total=None)

    async with httpx.AsyncClient(base_url=BASE) as client:
        fname, sz = await _download_one(client, entry, tmp_path, prog, task, dry_run=True)

    assert fname == "vol1.epub"
    assert sz is None
    assert not (tmp_path / "vol1.epub").exists()


def test_title_manifest_skip_done(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("elscione_dl.paths.library_root", lambda: tmp_path)
    tm = TitleManifest("MyTitle")
    assert not tm.is_done("vol1.epub")
    tm.mark_done("vol1.epub", "http://x/vol1.epub", 100)
    assert tm.is_done("vol1.epub")


def test_run_manifest_records(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("elscione_dl.paths.manifest_store", lambda: tmp_path)
    run = RunManifest()
    run.add_title("My Title")
    run.record_outcome("file.epub", "ok")
    run.record_outcome("file2.epub", "failed")
    run.finish()
    assert run.failed_files == ["file2.epub"]
