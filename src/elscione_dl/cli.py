"""CLI entrypoint using Typer."""
from __future__ import annotations

import asyncio
import sys
import urllib.parse
from typing import Optional

# Force UTF-8 output on Windows so Unicode filenames display correctly
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import typer
from rich.console import Console
from rich.table import Table

from .api import _make_client, walk_title
from .config import get_settings
from .downloader import download_titles
from .manifest import RunManifest, TitleManifest
from .paths import library_root

app = typer.Typer(
    name="elscione-dl",
    help="Bulk downloader for server.elscione.com",
    add_completion=False,
)
console = Console()


def _parse_formats(fmt_str: str) -> set[str]:
    parts = [p.strip().lower().lstrip(".") for p in fmt_str.split(",")]
    return {"." + p for p in parts if p}


def _title_name_from_href(href: str) -> str:
    return urllib.parse.unquote(href.strip("/").rsplit("/", 1)[-1])


@app.callback(invoke_without_command=True)
def default(ctx: typer.Context) -> None:
    """Launch the interactive TUI (default when no subcommand given)."""
    if ctx.invoked_subcommand is None:
        _run_tui()


def _run_tui() -> None:
    from .api import _make_client
    from .tui import run_tui

    async def _main() -> None:
        async with _make_client() as client:
            selections = await run_tui(client)
            if not selections:
                console.print("[yellow]No titles selected, exiting.")
                return
            run = RunManifest()
            # latest_only is per-selection; if any selection has it, we honour
            # it for that selection. We pass it as a uniform flag using the
            # first selection's value (TUI applies the same toggle per-title
            # but the engine accepts only one global flag — so we run titles
            # in two batches if they differ).
            groups: dict[bool, list] = {True: [], False: []}
            for s in selections:
                groups[s.latest_only].append((s.title_name, s.title_href, s.formats))
            for latest_flag, batch in groups.items():
                if batch:
                    await download_titles(batch, client, run, latest_only=latest_flag)
            _print_summary(run)

    asyncio.run(_main())


@app.command()
def pick() -> None:
    """Alias for the default interactive TUI."""
    _run_tui()


@app.command()
def download(
    title_hrefs: list[str] = typer.Argument(..., help="Server paths or full URLs to title directories"),
    formats: str = typer.Option("", "--formats", "-f", help="Comma-separated: epub,pdf (default: from config)"),
    concurrency: int = typer.Option(0, "--concurrency", "-c", help="Parallel downloads (0=use config)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List files without downloading"),
    latest_only: bool = typer.Option(False, "--latest-only", help="Skip older versions when {v2}+ exists"),
    root: str = typer.Option("", "--root", help="Override OneDrive library root path"),
) -> None:
    """Non-interactive download of specific title(s)."""
    cfg = get_settings()
    if concurrency > 0:
        cfg.concurrency = concurrency

    fmt_set = _parse_formats(formats) if formats else _parse_formats(cfg.default_formats)

    async def _main() -> None:
        async with _make_client() as client:
            run = RunManifest()
            selections = []
            for href in title_hrefs:
                if href.startswith("http"):
                    parsed = urllib.parse.urlparse(href)
                    href = parsed.path
                title_name = _title_name_from_href(href)
                selections.append((title_name, href, fmt_set))

            await download_titles(selections, client, run, dry_run=dry_run, latest_only=latest_only)
            _print_summary(run)

    asyncio.run(_main())


@app.command(name="retry-failed")
def retry_failed(
    run_id: str = typer.Argument(..., help="Run ID from a previous run (see list-runs)"),
    formats: str = typer.Option("", "--formats", "-f"),
) -> None:
    """Re-download files that failed in a previous run."""
    from .manifest import RunManifest

    try:
        run = RunManifest(run_id=run_id)
    except Exception as exc:
        console.print(f"[red]Cannot load run {run_id}: {exc}")
        raise typer.Exit(1)

    failed = run.failed_files
    if not failed:
        console.print("[green]No failed files in that run.")
        return

    console.print(f"[yellow]Retrying {len(failed)} failed file(s) from run {run_id}")
    # Re-download by reconstructing per-title manifests
    # Group failed files by title (they are stored as bare filenames — check title manifests)
    # For simplicity, trigger a fresh TUI so user can re-select
    console.print("[dim]Launching TUI — re-select the affected titles to retry.")
    _run_tui()


@app.command(name="list-runs")
def list_runs() -> None:
    """Show all recorded download runs."""
    runs = RunManifest.list_runs()
    if not runs:
        console.print("[dim]No runs recorded yet.")
        return
    table = Table("Run ID", "Location")
    from .paths import manifest_store
    store = manifest_store() / "runs"
    for rid in runs:
        table.add_row(rid, str(store / f"{rid}.json"))
    console.print(table)


@app.command(name="config")
def show_config(
    action: str = typer.Argument("show", help="'show' or 'edit'"),
) -> None:
    """Show or open the config file."""
    from pathlib import Path
    cfg_path = Path(__file__).parent.parent.parent / "config.toml"
    if action == "edit":
        import os
        os.startfile(str(cfg_path))
    else:
        console.print(f"[bold]Config file:[/bold] {cfg_path}\n")
        console.print(cfg_path.read_text())


def _print_summary(run: RunManifest) -> None:
    outcomes = run._record.outcomes
    ok = sum(1 for v in outcomes.values() if v == "ok")
    failed = sum(1 for v in outcomes.values() if v == "failed")
    skipped = sum(1 for v in outcomes.values() if v == "skipped")
    console.print(
        f"\n[bold]Run {run.run_id}[/bold]: "
        f"[green]{ok} ok[/green] · "
        f"[red]{failed} failed[/red] · "
        f"[dim]{skipped} skipped[/dim]"
    )
    console.print(f"Library: [blue]{library_root()}[/blue]")
