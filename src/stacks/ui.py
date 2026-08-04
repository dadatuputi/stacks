"""Shared visual language: theme, banner, panels, tables, and progress bars.

Everything "snazzy" about the CLI lives here so the rest of the codebase can
stay boring and testable.
"""

from __future__ import annotations

import random
from typing import Iterable, Optional

import pyfiglet
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

THEME = {
    "accent": "bold #7dd3fc",
    "accent2": "bold #c084fc",
    "success": "bold #4ade80",
    "warn": "bold #fbbf24",
    "danger": "bold #f87171",
    "dim": "grey58",
    "title": "bold #f0abfc",
}

console = Console(theme=Theme(THEME), highlight=False)

_BANNER_GRADIENT = ["#7dd3fc", "#a5b4fc", "#c084fc", "#e879f9", "#f0abfc"]


def banner(subtitle: str = "your Audible library, downloaded · decrypted · tagged · shelved") -> None:
    art = pyfiglet.figlet_format("stacks", font="slant")
    lines = [l for l in art.splitlines() if l.strip()]
    n = max(len(lines) - 1, 1)
    for i, line in enumerate(lines):
        color = _BANNER_GRADIENT[int(i / n * (len(_BANNER_GRADIENT) - 1))]
        console.print(Text(line, style=f"bold {color}"))
    console.print(f"  [dim]{subtitle}[/dim]\n")


def panel(body: str, title: Optional[str] = None, style: str = THEME["accent"]) -> None:
    console.print(Panel.fit(body, title=title, border_style=style, padding=(1, 2)))


def error(message: str) -> None:
    console.print(f"[danger]✗ {message}[/danger]")


def success(message: str) -> None:
    console.print(f"[success]✓ {message}[/success]")


def info(message: str) -> None:
    console.print(f"[accent]›[/accent] {message}")


def warn(message: str) -> None:
    console.print(f"[warn]![/warn] {message}")


def make_table(title: str, columns: Iterable[str]) -> Table:
    table = Table(title=title, title_style="title", header_style="accent", border_style="dim")
    for c in columns:
        table.add_column(c)
    return table


def download_progress() -> Progress:
    """A multi-task progress display suited to N concurrent file downloads."""
    return Progress(
        SpinnerColumn(style="accent"),
        TextColumn("[bold]{task.fields[label]}", justify="left"),
        BarColumn(bar_width=28, complete_style="accent2", finished_style="success"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


def step_progress(description: str) -> Progress:
    """A simple indeterminate/step progress bar for non-download work
    (tagging, moving files, probing audio, ...)."""
    return Progress(
        SpinnerColumn(style="accent"),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=28, complete_style="accent2", finished_style="success"),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
        transient=False,
    )


_TAGLINES = [
    "shelving your books, one ASIN at a time",
    "no more mystery-meat filenames",
    "chapters, covers, and copyright pages included",
    "narrator credits are people too",
    "the library sorts itself now",
]


def random_tagline() -> str:
    return random.choice(_TAGLINES)
