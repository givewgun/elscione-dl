"""Async download engine with resume, retry, throttle, and progress."""
from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Iterable

import httpx
from rich.console import Console
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

from .api import Entry, filter_latest_versions, walk_title
from .config import get_settings
from .manifest import RunManifest, TitleManifest
from .paths import title_dir

_RETRYABLE = (httpx.HTTPError, httpx.RemoteProtocolError, httpx.TimeoutException)


def _make_progress(console: Console) -> Progress:
    """Progress display sized for the *active* downloads only.

    Completed tasks are removed from the live area and logged via
    ``console.print``, so the live display stays bounded by ``concurrency``.
    """
    return Progress(
        TextColumn("[bold cyan]{task.description}", justify="left"),
        BarColumn(bar_width=30),
        "[progress.percentage]{task.percentage:>3.0f}%",
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
        expand=True,
    )


def _short(name: str, n: int = 60) -> str:
    return name if len(name) <= n else name[: n - 1] + "…"


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
        progress.update(task_id, total=1, completed=1)
        return filename, None

    await asyncio.sleep(random.uniform(cfg.delay_min_ms, cfg.delay_max_ms) / 1000)

    resume_from = part.stat().st_size if part.exists() else 0
    headers: dict[str, str] = {}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"

    async with client.stream("GET", entry.url, headers=headers) as resp:
        if resp.status_code == 416:
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
        raise ValueError(f"Size mismatch: expected {total}, got {final_size}")

    part.rename(dest)
    return filename, final_size


async def _download_task(
    client: httpx.AsyncClient,
    entry: Entry,
    dest_dir: Path,
    semaphore: asyncio.Semaphore,
    progress: Progress,
    console: Console,
    title_manifest: TitleManifest,
    run_manifest: RunManifest,
    dry_run: bool = False,
) -> None:
    cfg = get_settings()
    filename = entry.name

    if not dry_run and title_manifest.is_done(filename):
        console.print(f"[dim]⊘ skip[/dim] {_short(filename)} [dim](already done)[/dim]")
        run_manifest.record_outcome(filename, "skipped")
        return

    async with semaphore:
        # Add to live progress only when we actually start working
        task_id = progress.add_task(_short(filename), total=None)

        @retry(
            retry=retry_if_exception_type(_RETRYABLE),
            stop=stop_after_attempt(cfg.max_retries),
            wait=wait_exponential_jitter(initial=1, max=30),
            reraise=True,
        )
        async def _attempt() -> tuple[str, int | None]:
            return await _download_one(client, entry, dest_dir, progress, task_id, dry_run)

        try:
            fname, size = await _attempt()
            if not dry_run:
                title_manifest.mark_done(fname, entry.url, size)
            run_manifest.record_outcome(fname, "ok")
            console.print(f"[green]✓ done[/green] {_short(fname)}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (408, 429):
                retry_after = int(exc.response.headers.get("Retry-After", 5))
                console.print(f"[yellow]⏸  rate-limited[/yellow] {_short(filename)} — sleeping {retry_after}s")
                await asyncio.sleep(retry_after)
                try:
                    fname, size = await _download_one(client, entry, dest_dir, progress, task_id, dry_run)
                    if not dry_run:
                        title_manifest.mark_done(fname, entry.url, size)
                    run_manifest.record_outcome(fname, "ok")
                    console.print(f"[green]✓ done[/green] {_short(fname)}")
                except Exception as e2:
                    _record_failure(filename, entry.url, str(e2), title_manifest, run_manifest, console, dry_run)
            else:
                _record_failure(filename, entry.url, str(exc), title_manifest, run_manifest, console, dry_run)
        except Exception as exc:
            _record_failure(filename, entry.url, str(exc), title_manifest, run_manifest, console, dry_run)
        finally:
            try:
                progress.remove_task(task_id)
            except Exception:
                pass


def _record_failure(
    filename: str,
    url: str,
    reason: str,
    title_manifest: TitleManifest,
    run_manifest: RunManifest,
    console: Console,
    dry_run: bool,
) -> None:
    if not dry_run:
        title_manifest.mark_failed(filename, url, reason)
    run_manifest.record_outcome(filename, "failed")
    console.print(f"[red]✗ FAILED[/red] {_short(filename)} — {reason}")


async def download_titles(
    selections: list[tuple[str, str, set[str]]],  # (title_name, title_href, formats)
    client: httpx.AsyncClient,
    run_manifest: RunManifest,
    dry_run: bool = False,
    latest_only: bool = False,
) -> None:
    """Download all selected titles."""
    cfg = get_settings()
    semaphore = asyncio.Semaphore(cfg.concurrency)
    console = Console()

    # First pass: enumerate everything so we can show a total summary
    plan: list[tuple[str, Path, TitleManifest, list[Entry]]] = []
    for title_name, title_href, formats in selections:
        run_manifest.add_title(title_name)
        dest = title_dir(title_name)
        tm = TitleManifest(title_name)

        entries: list[Entry] = []
        async for e in walk_title(client, title_href, formats):
            entries.append(e)

        if latest_only:
            before = len(entries)
            entries = filter_latest_versions(entries)
            dropped = before - len(entries)
            if dropped:
                console.print(f"[dim]({title_name}: dropped {dropped} older version(s))[/dim]")

        if not entries:
            console.print(f"[yellow]No matching files in:[/yellow] {title_name}")
            continue

        plan.append((title_name, dest, tm, entries))

    if not plan:
        console.print("[yellow]Nothing to download.")
        return

    total_files = sum(len(entries) for _, _, _, entries in plan)
    console.print(
        f"[bold]Queued {total_files} file(s) across {len(plan)} title(s) "
        f"(concurrency={cfg.concurrency})[/bold]"
    )

    progress = _make_progress(console)
    with progress:
        tasks: list[asyncio.Task] = []
        for title_name, dest, tm, entries in plan:
            for entry in entries:
                tasks.append(asyncio.create_task(
                    _download_task(
                        client, entry, dest, semaphore, progress, console, tm, run_manifest, dry_run
                    )
                ))
        await asyncio.gather(*tasks, return_exceptions=False)

    run_manifest.finish()
