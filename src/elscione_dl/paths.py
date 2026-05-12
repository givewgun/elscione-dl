from __future__ import annotations

import os
import re
from pathlib import Path

from .config import get_settings

# Characters Windows forbids in file/folder names
_WIN_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Trailing dots/spaces are also illegal on Windows
_TRAIL = re.compile(r'[. ]+$')


def sanitise(name: str) -> str:
    """Replace Windows-illegal characters with '_', trim trailing dots/spaces."""
    result = _WIN_ILLEGAL.sub("_", name)
    result = _TRAIL.sub("", result)
    return result or "_"


def find_onedrive_root() -> Path:
    """Locate the OneDrive consumer root on Windows via registry / env."""
    cfg = get_settings()
    if cfg.onedrive_root:
        p = Path(cfg.onedrive_root)
        if p.exists():
            return p
        raise FileNotFoundError(f"Configured onedrive_root not found: {p}")

    # Try environment variables set by the OneDrive client
    for var in ("OneDriveConsumer", "OneDrive"):
        val = os.environ.get(var)
        if val:
            p = Path(val)
            if p.exists():
                return p

    # Fallback: common default paths
    home = Path.home()
    candidates = [
        home / "OneDrive",
        home / "OneDrive - Personal",
    ]
    for c in candidates:
        if c.exists():
            return c

    raise FileNotFoundError(
        "Cannot locate OneDrive root. "
        "Set onedrive_root in config.toml or ELSCIONE_ONEDRIVE_ROOT env var."
    )


def library_root() -> Path:
    """Return the library root (the 'manga novel' folder inside OneDrive Documents)."""
    cfg = get_settings()
    root = find_onedrive_root()
    lib = root / "Documents" / cfg.library_subdir
    lib.mkdir(parents=True, exist_ok=True)
    return lib


def title_dir(title_name: str) -> Path:
    """Return the target directory for a given title, creating it if needed."""
    safe = sanitise(title_name)
    d = library_root() / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def format_dir(title_name: str, ext: str) -> Path:
    """Return the per-format subdirectory for a title (e.g. .../Title/epub/).

    Files with unrecognised extensions go directly into the title directory.
    """
    known = {".epub", ".pdf", ".cbz", ".cbr", ".zip", ".mp3", ".m4a", ".m4b", ".opus", ".ogg", ".flac"}
    base = title_dir(title_name)
    if ext.lower() in known:
        d = base / ext.lstrip(".").lower()
        d.mkdir(parents=True, exist_ok=True)
        return d
    return base


def manifest_store() -> Path:
    """Return the hidden metadata directory inside the library root."""
    d = library_root() / ".elscione_dl"
    d.mkdir(parents=True, exist_ok=True)
    return d
