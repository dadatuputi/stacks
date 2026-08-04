"""Paths and persistent settings for stacks.

Everything lives under ``~/.stacks`` by default (override with the
``STACKS_HOME`` environment variable). Nothing here talks to the network —
it just knows where things go.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def home_dir() -> Path:
    override = os.environ.get("STACKS_HOME")
    root = Path(override).expanduser() if override else Path.home() / ".stacks"
    root.mkdir(parents=True, exist_ok=True)
    return root


def auth_file(profile: str = "default") -> Path:
    return home_dir() / f"auth-{profile}.json"


def cache_dir() -> Path:
    d = home_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def library_cache_file() -> Path:
    return cache_dir() / "library.json"


def chapters_cache_file() -> Path:
    return cache_dir() / "chapters.json"


def pdf_cache_dir() -> Path:
    d = cache_dir() / "pdfs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def activation_bytes_file() -> Path:
    return home_dir() / "activation_bytes"


def log_file() -> Path:
    return home_dir() / "stacks.log"


def settings_file() -> Path:
    return home_dir() / "settings.json"


@dataclass
class Settings:
    """User preferences, persisted as JSON. Every field has a sane default,
    so a fresh install works with zero configuration."""

    download_dir: str = str(Path.home() / "Audiobooks")
    quality: str = "high"          # "high" | "normal"
    workers: int = 4               # concurrent downloads
    auto_enrich: bool = True       # stamp metadata right after download
    auto_organize: bool = False    # move into Author/Book folders right after
    fetch_pdfs: bool = True
    cover_size: int = 2400
    locale: str = "us"             # audible marketplace: us, uk, de, fr, ca, au, in, it, es, jp, br

    @classmethod
    def load(cls) -> "Settings":
        p = settings_file()
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        settings_file().write_text(json.dumps(asdict(self), indent=2))
