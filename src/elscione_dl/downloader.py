"""Async download engine with resume, retry, throttle, and progress."""
from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path
from typing import Callable

import httpx
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .api import Entry
from .config import get_settings
from .manifest import RunManifest, TitleManifest
from .paths import title_dir

_RETRYABLE = (httpx.HTTPError, httpx.RemoteProtocolError, httpx.TimeoutException)

StatusCallback = Callable[[str, str], None]  # (filename, status)


def _make_progress() -> Progress:
    return Progress(
        TextColumn("[bold blue]{task.fields[title]}", justify="right"),
        TextColumn("[cyan]{task.description}"),
        BarColumn(bar_width=None),
        "[progress.percentage]{task.percentage:>3.0f}%",
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        expand=True,
    )


async def _download_one(
    client: httpx.AsyncClient,
    entry: Entry,
    dest_dir: Path,
    progress: Progress,
    task_id: TaskID,
    dry_run: bool = False,
) -> tuple[str, int | None]:
    """Download entry to dest_dir. Returns (filename, final_size)."""
    cfg = get_settings()
    filename = entry.name
    dest = dest_dir / filename
    part = dest_dir / (filename + ".part")

    if dry_run:
        progress.update(task_id, description=f"[dry-run] {filename}", completed=1, total=1)
        return filename, None

    # Jittered delay before download
    await asyncio.sleep(random.uniform(cfg.delay_min_ms, cfg.delay_max_ms) / 1000)

    # Determine resume offset
    resume_from = 0
    if part.exists():
        resume_from = part.stat().st_size

    headers: dict[str, str] = {}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"

    async with client.stream("GET", entry.url, headers=headers) as resp:
        if resp.status_code == 416:
            # Range not satisfiable — server already has full file
            part.rename(dest)
            return filename, resume_from

        resp.raise_for_status()

        total_raw = resp.headers.get("Content-Length")
        total = int(total_raw) + resume_from if total_raw else None
        progress.update(task_id, total=total, completed=resume_from)

        mode = "ab" if resume_from > 0 else "wb"
        with open(part, mode) as f:
            async for chunk in resp.aiter_bytes(65536):
                f.write(chunk)
                progress.advance(task_id, len(chunk))

    final_size = part.stat().st_size
    if total and final_size != total:
        raise ValueError(
            f"Size mismatch: expected {total}, got {final_size} for {filename}"
        )

    part.rename(dest)
    return filename, final_size


async def download_entry(
    client: httpx.AsyncClient,
    entry: Entry,
    dest_dir: Path,
    semaphore: asyncio.Semaphore,
    progress: Progress,
    title_manifest: TitleManifest,
    run_manifest: RunManifest,
    dry_run: bool = False,
) -> None:
    cfg = get_settings()
    filename = entry.name

    if not dry_run and title_manifest.is_done(filename):
        progress.console.print(f"[dim]skip {filename} (already downloaded)")
        run_manifest.record_outcome(filename, "skipped")
        return

    task_id = progress.add_task(filename, title=filename, total=None, start=False)

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(cfg.max_retries),
        wait=wait_exponential_jitter(initial=1, max=30),
        reraise=True,
    )
    async def _attempt() -> tuple[str, int | None]:
        progress.start_task(task_id)
        return await _download_one(client, entry, dest_dir, progress, task_id, dry_run)

    async with semaphore:
        try:
            fname, size = await _attempt()
            if not dry_run:
                title_manifest.mark_done(fname, entry.url, size)
            run_manifest.record_outcome(fname, "ok")
            progress.update(task_id, description=f"[green]done {filename}")
        except httpx.HTTPStatusError as exc:
            # 4xx errors (except 408/429) are fatal — don't retry
            if exc.response.status_code in (408, 429):
                retry_after = int(exc.response.headers.get("Retry-After", 5))
                progress.console.print(
                    f"[yellow]Rate-limited on {filename}, sleeping {retry_after}s"
                )
                await asyncio.sleep(retry_after)
                # One more attempt after honouring Retry-After
                try:
                    fname, size = await _download_one(client, entry, dest_dir, progress, task_id, dry_run)
                    if not dry_run:
                        title_manifest.mark_done(fname, entry.url, size)
                    run_manifest.record_outcome(fname, "ok")
                    return
                except Exception as e2:
                    reason = str(e2)
            else:
                reason = str(exc)
            if not dry_run:
                title_manifest.mark_failed(filename, entry.url, reason)
            run_manifest.record_outcome(filename, "failed")
            progress.update(task_id, description=f"[red]FAILED {filename}")
            progress.console.print(f"[red]Failed {filename}: {reason}")
        except Exception as exc:
            reason = str(exc)
            if not dry_run:
                title_manifest.mark_failed(filename, entry.url, reason)
            run_manifest.record_outcome(filename, "failed")
            progress.update(task_id, description=f"[red]FAILED {filename}")
            progress.console.print(f"[red]Failed {filename}: {reason}")


async def download_titles(
    selections: list[tuple[str, str, set[str]]],  # (title_name, title_href, formats)
    client: httpx.AsyncClient,
    run_manifest: RunManifest,
    dry_run: bool = False,
) -> None:
    """
    Download all selected titles.
    selections: list of (human title name, server href, {'.epub', '.pdf', ...})
    """
    from .api import walk_title

    cfg = get_settings()
    semaphore = asyncio.Semaphore(cfg.concurrency)

    with _make_progress() as progress:
        tasks: list[asyncio.Task] = []

        for title_name, title_href, formats in selections:
            run_manifest.add_title(title_name)
            dest = title_dir(title_name)
            tm = TitleManifest(title_name)

            entries: list[Entry] = []
            async for e in walk_title(client, title_href, formats):
                entries.append(e)

            if not entries:
                progress.console.print(f"[yellow]No matching files in: {title_name}")
                continue

            for entry in entries:
                t = asyncio.create_task(
                    download_entry(
                        client, entry, dest, semaphore, progress, tm, run_manifest, dry_run
                    )
                )
                tasks.append(t)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=False)

    run_manifest.finish()
