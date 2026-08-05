"""Reconcile your Audible library against the .m4b files on disk.

Answers the question `stacks missing` exists to answer: of everything the
account owns, which titles aren't downloaded yet? Pure and read-only — it
scans the filesystem and diffs sets; it never touches the network or moves a
file. ASIN resolution reuses ``organizer.file_asin`` (tag → filename), with
an optional deep pass through ``matcher.match_file`` for files that carry no
ASIN at all (ripped elsewhere, tagged by another tool).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import catalog, organizer


def scan_local(root: Path, deep: bool = False, items: Optional[list[dict]] = None) -> tuple[dict[str, list[Path]], list[Path]]:
    """Walk ``root`` recursively for .m4b files and resolve each to an ASIN.

    Recursive on purpose: raw downloads sit flat in the download dir, but the
    organizer nests them as ``Author/Title/Title.m4b`` — one scan has to see
    both. Returns ``({asin: [paths]}, [unresolved paths])``.

    ``deep`` falls back to the fuzzy ``matcher`` for files that ``file_asin``
    can't place; it needs ``items`` (the cached library) and runs an ffprobe
    per file, so it's opt-in.
    """
    by_asin: dict[str, list[Path]] = defaultdict(list)
    unresolved: list[Path] = []

    deep_index = None
    if deep and items:
        from . import matcher

        deep_index = matcher.build_index(items)

    for p in sorted(root.rglob("*.m4b")):
        asin = organizer.file_asin(p)
        if not asin and deep_index is not None:
            from . import matcher

            result = matcher.match_file(p, *deep_index)
            if result.item is not None:
                asin = result.item["asin"]
        if asin:
            by_asin[asin].append(p)
        else:
            unresolved.append(p)

    return dict(by_asin), unresolved


@dataclass
class ReconcileReport:
    missing: list[dict] = field(default_factory=list)       # library items not on disk
    present: list[dict] = field(default_factory=list)       # library items found on disk
    unknown: list[str] = field(default_factory=list)        # ASINs on disk but not in the library
    unresolved: list[Path] = field(default_factory=list)    # .m4b files with no resolvable ASIN
    duplicates: dict[str, list[Path]] = field(default_factory=dict)  # ASINs present more than once

    @property
    def total_library(self) -> int:
        return len(self.missing) + len(self.present)


def reconcile(items: list[dict], found: dict[str, list[Path]], unresolved: Optional[list[Path]] = None) -> ReconcileReport:
    """Diff the cached library against what ``scan_local`` found on disk."""
    by_asin = {i["asin"]: i for i in items}
    present_asins = set(found)

    missing = [it for a, it in by_asin.items() if a not in present_asins]
    present = [by_asin[a] for a in present_asins if a in by_asin]
    unknown = sorted(a for a in present_asins if a not in by_asin)
    duplicates = {a: paths for a, paths in found.items() if len(paths) > 1}

    missing.sort(key=lambda it: (catalog.author_of(it).lower(), (it.get("title") or "").lower()))
    present.sort(key=lambda it: (catalog.author_of(it).lower(), (it.get("title") or "").lower()))

    return ReconcileReport(
        missing=missing,
        present=present,
        unknown=unknown,
        unresolved=list(unresolved or []),
        duplicates=duplicates,
    )
