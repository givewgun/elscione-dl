"""Interactive Textual TUI for browsing catalog and selecting titles."""
from __future__ import annotations

import asyncio
import logging
import traceback
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
)

from .api import Entry, list_dir, probe_title_formats
from .catalog import fetch_catalog

# Write errors to a log file next to config.toml so users can check it
_LOG_FILE = Path(__file__).parent.parent.parent / "elscione_dl_errors.log"
logging.basicConfig(
    filename=str(_LOG_FILE),
    level=logging.ERROR,
    format="%(asctime)s %(levelname)s %(message)s",
)
_log = logging.getLogger(__name__)


def _log_exc(context: str, exc: BaseException) -> None:
    _log.error("%s: %s\n%s", context, exc, traceback.format_exc())


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TitleSelection:
    title_name: str
    title_href: str
    formats: set[str] = field(default_factory=lambda: {".epub"})
    latest_only: bool = True


@dataclass
class FormatChoice:
    formats: set[str]
    latest_only: bool


# ---------------------------------------------------------------------------
# Format picker modal
# ---------------------------------------------------------------------------

class FormatModal(ModalScreen[FormatChoice | None]):
    """Modal to choose format preset + latest-only for a single title."""

    DEFAULT_CSS = """
    FormatModal {
        align: center middle;
    }
    #modal-box {
        width: 64;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #modal-title {
        text-align: center;
        margin-bottom: 1;
        color: $accent;
    }
    #modal-subtitle {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }
    #preset-buttons {
        height: 3;
        align: center middle;
        margin-bottom: 1;
    }
    #preset-buttons Button {
        margin: 0 1;
    }
    #latest-row {
        height: 1;
        align: center middle;
        margin-bottom: 1;
    }
    #cancel-row {
        align: center middle;
    }
    """

    def __init__(self, title_name: str, available: set[str]) -> None:
        super().__init__()
        self._title_name = title_name
        self._available = available

    def compose(self) -> ComposeResult:
        with Container(id="modal-box"):
            yield Label(f"[b]{self._title_name[:55]}[/b]", id="modal-title")
            avail_list = ", ".join(sorted(e.lstrip(".").upper() for e in self._available))
            yield Label(f"Available: {avail_list or 'unknown'}", id="modal-subtitle")
            with Horizontal(id="preset-buttons"):
                if ".epub" in self._available:
                    yield Button("EPUB only", variant="primary", id="preset-epub")
                if ".pdf" in self._available:
                    yield Button("PDF only", variant="primary", id="preset-pdf")
                yield Button("Both", variant="default", id="preset-both")
            with Horizontal(id="latest-row"):
                yield Checkbox("Latest version only (skip older {v1} when {v2} exists)",
                               value=True, id="cb-latest")
            with Horizontal(id="cancel-row"):
                yield Button("Cancel", variant="error", id="cancel")

    def _latest(self) -> bool:
        try:
            return self.query_one("#cb-latest", Checkbox).value
        except Exception:
            return True

    @on(Button.Pressed, "#preset-epub")
    def _preset_epub(self) -> None:
        self.dismiss(FormatChoice(formats={".epub"}, latest_only=self._latest()))

    @on(Button.Pressed, "#preset-pdf")
    def _preset_pdf(self) -> None:
        self.dismiss(FormatChoice(formats={".pdf"}, latest_only=self._latest()))

    @on(Button.Pressed, "#preset-both")
    def _preset_both(self) -> None:
        chosen = self._available & {".epub", ".pdf"} or self._available.copy()
        self.dismiss(FormatChoice(formats=chosen, latest_only=self._latest()))

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Main TUI app
# ---------------------------------------------------------------------------

class ELSApp(App):
    """Elscione bulk downloader TUI."""

    CSS = """
    Screen {
        layout: horizontal;
    }

    #left-pane {
        width: 36;
        min-width: 24;
        border: solid $primary;
        padding: 0 1;
        overflow-x: hidden;
    }

    #left-pane Label {
        width: 100%;
        padding: 0 1;
    }

    #left-pane ListView {
        width: 100%;
    }

    #left-pane ListItem {
        width: 100%;
        overflow: hidden;
    }

    #right-pane {
        width: 1fr;
        layout: vertical;
    }

    #search-bar {
        dock: top;
        height: 3;
        padding: 0 1;
    }

    #title-list-container {
        height: 1fr;
        border: solid $accent;
    }

    #bottom-bar {
        dock: bottom;
        height: 3;
        padding: 0 1;
        layout: horizontal;
        align: left middle;
    }

    #selection-count {
        width: 1fr;
        content-align: left middle;
    }

    #loading {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    ListView {
        height: 100%;
    }

    ListItem.checked Label {
        color: $success;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("d", "start_download", "Download"),
        Binding("r", "refresh_catalog", "Refresh"),
        Binding("escape", "clear_selection", "Clear"),
    ]

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__()
        self._client = client
        self._catalog: dict[str, list[Entry]] = {}
        self._current_cat: str | None = None
        self._displayed_titles: list[Entry] = []
        self._selections: dict[str, TitleSelection] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="left-pane"):
                yield Label("[b]Categories[/b]")
                yield ListView(id="cat-list")
            with Vertical(id="right-pane"):
                yield Input(placeholder="Search titles...", id="search-bar")
                with ScrollableContainer(id="title-list-container"):
                    yield ListView(id="title-list")
                with Horizontal(id="bottom-bar"):
                    yield Label("0 title(s) selected", id="selection-count")
                    yield Button("Download selected", variant="success", id="btn-download")
        yield Footer()

    def on_mount(self) -> None:
        self._load_catalog()

    @work(exclusive=True, thread=False)
    async def _load_catalog(self, force: bool = False) -> None:
        try:
            cat_list = self.query_one("#cat-list", ListView)
            await cat_list.clear()
        except Exception:
            pass

        try:
            self._catalog = await fetch_catalog(self._client, force_refresh=force)
        except Exception as exc:
            _log_exc("_load_catalog", exc)
            self.notify(f"Failed to load catalog — see elscione_dl_errors.log", severity="error", timeout=8)
            return

        try:
            cat_list = self.query_one("#cat-list", ListView)
            for cat_href in sorted(self._catalog.keys()):
                name = urllib.parse.unquote(cat_href.strip("/").rsplit("/", 1)[-1])
                cat_list.append(ListItem(Label(name)))
            total = sum(len(v) for v in self._catalog.values())
            self.notify(f"Loaded {total} titles", timeout=3)
        except Exception as exc:
            _log_exc("_load_catalog (render)", exc)
            self.notify("Error rendering categories — see log", severity="warning")

    @on(ListView.Selected, "#cat-list")
    def on_cat_selected(self, event: ListView.Selected) -> None:
        try:
            idx = event.list_view.index
            if idx is None:
                return
            cats = sorted(self._catalog.keys())
            if 0 <= idx < len(cats):
                self._current_cat = cats[idx]
                # Reset search and clear current title list before re-rendering
                search = self.query_one("#search-bar", Input)
                search.value = ""
                self._refresh_title_list()
        except Exception as exc:
            _log_exc("on_cat_selected", exc)
            self.notify("Error selecting category — see log", severity="warning")

    @on(Input.Changed, "#search-bar")
    def on_search(self, event: Input.Changed) -> None:
        try:
            self._refresh_title_list(query=event.value)
        except Exception as exc:
            _log_exc("on_search", exc)

    @work(exclusive=True, thread=False, group="refresh-titles")
    async def _refresh_title_list(self, query: str = "") -> None:
        if not self._current_cat:
            return
        try:
            titles = self._catalog.get(self._current_cat, [])
            q = query.lower()
            self._displayed_titles = [
                t for t in titles
                if not q or q in urllib.parse.unquote(t.href).lower()
            ]

            title_list = self.query_one("#title-list", ListView)
            # Await the clear so old items are fully removed before we mount new ones
            await title_list.clear()
            for t in self._displayed_titles:
                name = urllib.parse.unquote(t.href.strip("/").rsplit("/", 1)[-1])
                selected = t.href in self._selections
                prefix = "✓ " if selected else "  "
                item = ListItem(Label(prefix + name))
                if selected:
                    item.add_class("checked")
                title_list.append(item)
        except Exception as exc:
            _log_exc("_refresh_title_list", exc)
            self.notify("Error refreshing title list — see log", severity="warning")

    @on(ListView.Selected, "#title-list")
    def on_title_selected(self, event: ListView.Selected) -> None:
        try:
            idx = event.list_view.index
            if idx is None:
                return
            if 0 <= idx < len(self._displayed_titles):
                self._toggle_title(self._displayed_titles[idx])
        except Exception as exc:
            _log_exc("on_title_selected", exc)
            self.notify("Error selecting title — see log", severity="warning")

    def _toggle_title(self, entry: Entry) -> None:
        try:
            title_name = urllib.parse.unquote(entry.href.strip("/").rsplit("/", 1)[-1])
            if entry.href in self._selections:
                del self._selections[entry.href]
                self._refresh_title_list(self.query_one("#search-bar", Input).value)
                self._update_count()
                return
            self._probe_and_add(entry, title_name)
        except Exception as exc:
            _log_exc("_toggle_title", exc)
            self.notify("Error toggling title — see log", severity="warning")

    @work(exclusive=False, thread=False)
    async def _probe_and_add(self, entry: Entry, title_name: str) -> None:
        self.notify(f"Probing formats for {title_name[:35]}…", timeout=5)
        try:
            available = await probe_title_formats(self._client, entry.href)
        except Exception as exc:
            _log_exc(f"probe_title_formats({entry.href})", exc)
            available = {".epub", ".pdf"}
            self.notify("Could not probe formats, defaulting to EPUB+PDF", severity="warning", timeout=4)

        if not available:
            available = {".epub", ".pdf"}

        try:
            choice = await self.push_screen_wait(FormatModal(title_name, available))
        except Exception as exc:
            _log_exc("push_screen_wait FormatModal", exc)
            choice = None

        try:
            if choice is not None and choice.formats:
                self._selections[entry.href] = TitleSelection(
                    title_name=title_name,
                    title_href=entry.href,
                    formats=choice.formats,
                    latest_only=choice.latest_only,
                )
                fmt_label = ", ".join(sorted(e.lstrip(".") for e in choice.formats))
                latest_label = " (latest)" if choice.latest_only else ""
                self.notify(f"Added: {title_name[:35]} [{fmt_label}{latest_label}]", timeout=4)
            self._refresh_title_list(self.query_one("#search-bar", Input).value)
            self._update_count()
        except Exception as exc:
            _log_exc("_probe_and_add (post-modal)", exc)

    def _update_count(self) -> None:
        try:
            n = len(self._selections)
            self.query_one("#selection-count", Label).update(f"[b]{n}[/b] title(s) selected")
        except Exception as exc:
            _log_exc("_update_count", exc)

    @on(Button.Pressed, "#btn-download")
    def on_download_pressed(self) -> None:
        self.action_start_download()

    def action_start_download(self) -> None:
        if not self._selections:
            self.notify("No titles selected.", severity="warning")
            return
        self.exit(list(self._selections.values()))

    def action_refresh_catalog(self) -> None:
        self._load_catalog(force=True)

    def action_clear_selection(self) -> None:
        try:
            self._selections.clear()
            self._refresh_title_list(self.query_one("#search-bar", Input).value)
            self._update_count()
        except Exception as exc:
            _log_exc("action_clear_selection", exc)


def _href_id(href: str) -> str:
    import hashlib
    return hashlib.md5(href.encode()).hexdigest()[:12]


async def run_tui(client: httpx.AsyncClient) -> list[TitleSelection]:
    """Launch the TUI and return the user's selections. Returns [] on cancel."""
    try:
        app = ELSApp(client)
        result = await app.run_async()
        if isinstance(result, list):
            return result
        return []
    except Exception as exc:
        _log_exc("run_tui", exc)
        return []
