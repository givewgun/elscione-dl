"""Per-title and per-run download manifests for resume and audit."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .paths import manifest_store, title_dir


@dataclass
class FileRecord:
    url: str
    size: int | None = None
    completed_at: str | None = None
    failed_reason: str | None = None

    @property
    def done(self) -> bool:
        return self.completed_at is not None

    @property
    def failed(self) -> bool:
        return self.failed_reason is not None


class TitleManifest:
    """Tracks downloaded files for a single title."""

    def __init__(self, title_name: str) -> None:
        self.title_name = title_name
        self._path = title_dir(title_name) / "_manifest.json"
        self._records: dict[str, FileRecord] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for fname, data in raw.items():
                self._records[fname] = FileRecord(**data)

    def save(self) -> None:
        data = {k: asdict(v) for k, v in self._records.items()}
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def is_done(self, filename: str) -> bool:
        r = self._records.get(filename)
        return r is not None and r.done

    def mark_done(self, filename: str, url: str, size: int | None) -> None:
        self._records[filename] = FileRecord(
            url=url,
            size=size,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        self.save()

    def mark_failed(self, filename: str, url: str, reason: str) -> None:
        self._records[filename] = FileRecord(url=url, failed_reason=reason)
        self.save()

    def pending_files(self, filenames: list[str]) -> list[str]:
        return [f for f in filenames if not self.is_done(f)]

    @property
    def failed_files(self) -> list[str]:
        return [k for k, v in self._records.items() if v.failed]


@dataclass
class RunRecord:
    run_id: str
    started_at: str
    titles: list[str] = field(default_factory=list)
    outcomes: dict[str, str] = field(default_factory=dict)  # filename -> "ok"/"failed"/"skipped"
    finished_at: str | None = None


class RunManifest:
    """Global run log for retry-failed support."""

    def __init__(self, run_id: str | None = None) -> None:
        ts = datetime.now(timezone.utc)
        self.run_id = run_id or ts.strftime("%Y%m%dT%H%M%SZ")
        self._path = manifest_store() / "runs" / f"{self.run_id}.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._record = RunRecord(
            run_id=self.run_id,
            started_at=ts.isoformat(),
        )
        if run_id and self._path.exists():
            self._load()

    def _load(self) -> None:
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._record = RunRecord(**raw)

    def add_title(self, title: str) -> None:
        if title not in self._record.titles:
            self._record.titles.append(title)
        self._save()

    def record_outcome(self, filename: str, outcome: str) -> None:
        self._record.outcomes[filename] = outcome
        self._save()

    def finish(self) -> None:
        self._record.finished_at = datetime.now(timezone.utc).isoformat()
        self._save()

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(asdict(self._record), indent=2), encoding="utf-8"
        )

    @property
    def failed_files(self) -> list[str]:
        return [k for k, v in self._record.outcomes.items() if v == "failed"]

    @classmethod
    def list_runs(cls) -> list[str]:
        runs_dir = manifest_store() / "runs"
        if not runs_dir.exists():
            return []
        return sorted((p.stem for p in runs_dir.glob("*.json")), reverse=True)
