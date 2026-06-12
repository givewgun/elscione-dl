"""Tests for resume logic and retry behaviour.

The download path streams through a curl_cffi ``AsyncSession``. Tests use a
minimal fake that mirrors the bits ``_download_one`` touches: an async
``stream()`` context manager whose response exposes ``status_code``,
``headers``, ``raise_for_status()`` and ``aiter_content()``.
"""
from pathlib import Path

import pytest

from elscione_dl.api import Entry
from elscione_dl.downloader import _download_one
from elscione_dl.manifest import RunManifest, TitleManifest

BASE = "https://server.elscione.com"


def _make_entry(href: str = "/Novels/Title/vol1.epub", size: int = 100) -> Entry:
    return Entry(href=href, is_dir=False, size=size, mtime=0)


class _FakeResp:
    def __init__(self, status: int, content: bytes, headers: dict[str, str]):
        self.status_code = status
        self.headers = headers
        self._content = content

    def raise_for_status(self):
        if not (200 <= self.status_code < 400):
            from curl_cffi.requests.exceptions import HTTPError
            raise HTTPError(f"HTTP {self.status_code}", 0, self)

    async def aiter_content(self, chunk_size: int = 8192):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i : i + chunk_size]


class _FakeStream:
    def __init__(self, resp: _FakeResp):
        self._resp = resp

    async def __aenter__(self) -> _FakeResp:
        return self._resp

    async def __aexit__(self, *exc) -> bool:
        return False


class _FakeClient:
    """Stand-in for curl_cffi AsyncSession; records the headers it was sent."""

    def __init__(self, status: int, content: bytes, headers: dict[str, str]):
        self._resp_args = (status, content, headers)
        self.last_request_headers: dict[str, str] | None = None

    def stream(self, method: str, url: str, headers: dict[str, str] | None = None):
        self.last_request_headers = headers
        return _FakeStream(_FakeResp(*self._resp_args))


@pytest.mark.asyncio
async def test_download_creates_file(tmp_path: Path):
    entry = _make_entry()
    content = b"A" * 100
    client = _FakeClient(200, content, {"Content-Length": str(len(content))})

    from rich.progress import Progress
    prog = Progress()
    task = prog.add_task("test", total=None)

    fname, sz = await _download_one(client, entry, tmp_path, prog, task)

    assert fname == "vol1.epub"
    assert sz == 100
    assert (tmp_path / "vol1.epub").read_bytes() == content


@pytest.mark.asyncio
async def test_download_resumes_partial(tmp_path: Path):
    entry = _make_entry()
    full_content = b"A" * 50 + b"B" * 50
    # Pre-create a partial file
    part = tmp_path / "vol1.epub.part"
    part.write_bytes(b"A" * 50)

    client = _FakeClient(206, b"B" * 50, {"Content-Length": "50", "Accept-Ranges": "bytes"})

    from rich.progress import Progress
    prog = Progress()
    task = prog.add_task("test", total=None)

    fname, sz = await _download_one(client, entry, tmp_path, prog, task)

    assert (tmp_path / "vol1.epub").read_bytes() == full_content
    # Resume must request the remaining byte range
    assert client.last_request_headers == {"Range": "bytes=50-"}


@pytest.mark.asyncio
async def test_dry_run_no_file(tmp_path: Path):
    entry = _make_entry()
    client = _FakeClient(200, b"", {})
    from rich.progress import Progress
    prog = Progress()
    task = prog.add_task("test", total=None)

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
