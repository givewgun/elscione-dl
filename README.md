# elscione-dl

Bulk downloader for [server.elscione.com](https://server.elscione.com) — light novels and manga.
Downloads EPUB/PDF files directly into your OneDrive folder so they sync automatically.

---

## One-time setup (Windows)

### Step 1 — Install Python

If you don't have Python yet:

1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Download the latest **Python 3.11+** installer
3. Run it — **tick "Add Python to PATH"** before clicking Install

Verify it worked by opening a terminal and running:
```
python --version
```
You should see something like `Python 3.13.x`.

### Step 2 — Install elscione-dl

Double-click **`setup_windows.bat`** in the `crawler` folder.

It installs all dependencies automatically. You only need to do this once.

---

## Running the tool

### Option A — Double-click launcher (easiest)

Double-click **`run.bat`** to open the interactive catalog browser.

### Option B — Terminal

Open a terminal (PowerShell or Command Prompt) in the `crawler` folder and run:

```
python -m elscione_dl
```

> **Tip:** In File Explorer, Shift+Right-click inside the `crawler` folder → "Open PowerShell window here"

---

## Interactive browser (TUI)

When you launch `elscione-dl` or `run.bat`, a full-screen browser opens:

```
┌─ Categories ──────────┐  Search titles...
│ > Officially Translated│ ┌──────────────────────────────────────────┐
│   Manga                │ │   86                                     │
│   LNWNCentral Dump     │ │   A Certain Magical Index                │
│   Books                │ │ ✓ I'm in Love With the Villainess        │
│                        │ │   Mushoku Tensei                         │
│                        │ │   Overlord                               │
└────────────────────────┘ └──────────────────────────────────────────┘
  1 title(s) selected                        [ Download selected ]
```

**How to use:**

1. **Click a category** on the left (e.g. "Officially Translated").
2. **Type to search** titles — the list filters as you type.
3. **Click a title** to select it. A popup asks which formats to download:
   ```
   ┌─ Formats ──────────────────────────┐
   │  [x] EPUB                          │
   │  [ ] PDF                           │
   │        [ Confirm ]  [ Cancel ]     │
   └────────────────────────────────────┘
   ```
4. **Repeat** for as many titles as you want.
5. **Press `d`** or click **Download selected** to start downloading.

Progress bars show for each file. Files land in `OneDrive\Documents\manga novel\`.

**Keyboard shortcuts:**

| Key | Action |
|-----|--------|
| `d` | Start downloading selected titles |
| `r` | Refresh the catalog from the server |
| `Escape` | Clear all selections |
| `q` | Quit |

---

## Download a title directly (no browser)

Open PowerShell in the `crawler` folder and run:

```powershell
python -m elscione_dl download "/Officially Translated Light Novels/Overlord/" --formats epub
```

Or edit **`download.bat`** — change the `TITLE` and `FORMATS` lines at the top, then double-click it.

### Format options

| Flag | What it downloads |
|------|-------------------|
| `--formats epub` | EPUB files only |
| `--formats pdf` | PDF files only |
| `--formats epub,pdf` | Both |

### More examples

```powershell
# Download two titles at once
python -m elscione_dl download `
  "/Officially Translated Light Novels/86/" `
  "/Officially Translated Light Novels/Mushoku Tensei/" `
  --formats epub

# Use the full URL from your browser
python -m elscione_dl download "https://server.elscione.com/Officially%20Translated%20Light%20Novels/Overlord/" --formats epub

# See what would be downloaded without saving anything
python -m elscione_dl download "/Officially Translated Light Novels/Overlord/" --formats epub --dry-run
```

---

## Where files are saved

```
C:\Users\<you>\OneDrive\Documents\manga novel\
└── Overlord\
    ├── Overlord Volume 01.epub
    ├── Overlord Volume 02.epub
    └── ...
```

OneDrive picks up new files automatically and syncs them to the cloud.

A `_manifest.json` file is created inside each title folder. It tracks which files are done, so **re-running the same download safely skips already-completed files**.

---

## Resuming interrupted downloads

Downloads write to a `.part` file first, renamed only when complete. If you Ctrl+C or lose connection mid-download:

1. Just re-run the same command.
2. The tool detects the `.part` file and resumes from where it stopped.

---

## Retrying failed downloads

Each run is logged. List past runs:

```powershell
python -m elscione_dl list-runs
```

Retry everything that failed in a specific run:

```powershell
python -m elscione_dl retry-failed 20260506T100420Z
```

---

## Configuration

Edit **`config.toml`** in the `crawler` folder to change defaults:

```toml
[elscione]
concurrency    = 3        # parallel downloads (1–10)
delay_min_ms   = 250      # min pause between requests (ms)
delay_max_ms   = 750      # max pause (random jitter)
max_retries    = 5        # retry attempts per file before giving up
default_formats = "epub"  # used when --formats is not given

[paths]
onedrive_root  = ""       # leave blank to auto-detect; or set to e.g. D:\OneDrive
library_subdir = "manga novel"
```

View current settings:
```powershell
python -m elscione_dl config show
```

Open `config.toml` in Notepad:
```powershell
python -m elscione_dl config edit
```

---

## Troubleshooting

**"elscione-dl is not recognised"**
Run `setup_windows.bat` first. If it still fails, make sure Python was installed with "Add to PATH" ticked.

**TUI doesn't display properly**
Use [Windows Terminal](https://aka.ms/terminal) instead of the old Command Prompt — it has full Unicode and colour support.

**"No matching files" for a title**
Check the path is correct. Copy it from the browser URL bar, then URL-decode spaces (replace `%20` with a space). Pass the full path including the leading `/`.

**OneDrive folder not found**
Set `onedrive_root` in `config.toml` to your OneDrive path, e.g.:
```toml
[paths]
onedrive_root = "C:\Users\YourName\OneDrive"
```

---

## All commands

```
python -m elscione_dl                          Open the interactive TUI browser
python -m elscione_dl pick                     Same as above
python -m elscione_dl download <path(s)>       Download specific titles non-interactively
python -m elscione_dl retry-failed <run-id>    Retry files that failed in a previous run
python -m elscione_dl list-runs                Show all recorded run IDs
python -m elscione_dl config show              Print current configuration
python -m elscione_dl config edit              Open config.toml in default editor
```

Flags for `download`:

```
--formats epub,pdf    Formats to download (default: from config.toml)
--concurrency 3       Number of parallel downloads
--dry-run             List files without downloading anything
--root <path>         Override the OneDrive library root for this run
```
