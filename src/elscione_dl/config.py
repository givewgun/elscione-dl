from __future__ import annotations

import tomllib
from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_CONFIG_FILE = Path(__file__).parent.parent.parent / "config.toml"


def _load_toml() -> dict:
    if _CONFIG_FILE.exists():
        with open(_CONFIG_FILE, "rb") as f:
            return tomllib.load(f)
    return {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ELSCIONE_", env_nested_delimiter="__")

    base_url: str = "https://server.elscione.com"
    concurrency: int = 3
    delay_min_ms: int = 250
    delay_max_ms: int = 750
    max_retries: int = 5
    default_formats: str = "epub"

    onedrive_root: str = ""
    library_subdir: str = "manga novel"

    @field_validator("concurrency")
    @classmethod
    def clamp_concurrency(cls, v: int) -> int:
        return max(1, min(v, 10))

    @classmethod
    def from_toml(cls) -> "Settings":
        raw = _load_toml()
        merged: dict = {}
        merged.update(raw.get("elscione", {}))
        merged.update(raw.get("paths", {}))
        return cls(**merged)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_toml()
    return _settings
