"""Lay .m4b files out as <Author>/<Author> - <Title>/ and resolve
author-name collisions and same-book-different-edition collisions.

Disambiguation ladder when author+title collide: base -> +(Narrator) ->
+(Year) -> +[ASIN]. Only as much suffix as needed to make the path unique.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mutagen.mp4 import MP4

from .utils import ASIN_IN_NAME, sanitize, same_person

FF = "com.apple.iTunes"


def suggest_aliases(items: list[dict]) -> dict:
    """Group author-name spelling variants into clusters and propose a
    canonical spelling for each. Conservative by design — review the
    output; under-merging is safer than merging two real people."""
    names = Counter()
    for it in items:
        for a in it.get("authors") or []:
            names[a["name"]] += 1

    clusters: list[list[str]] = []
    for name in sorted(names, key=lambda n: (-names[n], n)):
        for c in clusters:
            if any(same_person(name, m) for m in c):
                c.append(name)
                break
        else:
            clusters.append([name])

    from .utils import HONORIFICS, ROLE_SUFFIX, name_parts

    def canon_rank(n: str):
        has_role = bool(ROLE_SUFFIX.search(n))
        has_hon = bool(HONORIFICS.search(n))
        _, given = name_parts(n)
        full_words = sum(1 for g in given if len(g) > 1)
        return (has_role, has_hon, -full_words, -names[n], n)

    out = {}
    for c in clusters:
        if len(c) < 2:
            continue
        canon = min(c, key=canon_rank)
        for n in c:
            if n != canon:
                out[n] = canon
    return out


def load_aliases(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def file_asin(path: Path) -> Optional[str]:
    try:
        t = MP4(path).tags or {}
    except Exception:  # noqa: BLE001
        t = {}
    v = t.get(f"----:{FF}:ASIN")
    if v:
        raw = v[0]
        return (raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)).strip()
    if m := ASIN_IN_NAME.search(path.name):
        return m.group(1)
    return None


@dataclass
class PlanEntry:
    src: Path
    asin: str
    item: dict
    author: str
    title: str
    narrators: list[str]
    year: str
    base: str
    book: str = ""
    why: str = ""

    @property
    def dir(self) -> Path:
        return Path(self.author) / self.book

    @property
    def m4b_name(self) -> str:
        return f"{self.book}.m4b"

    @property
    def pdf_name(self) -> str:
        return f"{self.book}.pdf"


def plan(by_asin: dict, files: dict[Path, str], aliases: dict) -> list[PlanEntry]:
    entries: list[PlanEntry] = []
    for p, asin in files.items():
        it = by_asin[asin]
        raw_authors = [a["name"] for a in it.get("authors") or []]
        authors = [aliases.get(a, a) for a in raw_authors]
        author = sanitize(authors[0] if authors else "Unknown Author", 60)
        title = sanitize(it.get("title") or asin, 90)
        narrators = [n["name"] for n in it.get("narrators") or []]
        year = ((it.get("publication_datetime") or it.get("issue_date") or it.get("release_date") or "")[:4])
        entries.append(PlanEntry(src=p, asin=asin, item=it, author=author, title=title, narrators=narrators, year=year, base=f"{author} - {title}"))

    groups: dict[tuple, list[PlanEntry]] = defaultdict(list)
    for e in entries:
        groups[(e.author, e.base)].append(e)

    for (author, base), grp in groups.items():
        if len(grp) == 1:
            grp[0].book, grp[0].why = base, ""
            continue
        for level in ("narrator", "year", "asin"):
            names = []
            for e in grp:
                if level == "narrator":
                    n = "; ".join(e.narrators[:2])
                    suffix = f" ({sanitize(n, 50)})" if n else ""
                elif level == "year":
                    suffix = f" ({e.year})" if e.year else ""
                else:
                    suffix = f" [{e.asin}]"
                names.append(base + suffix)
            if len(set(names)) == len(grp) and all(n != base for n in names):
                for e, n in zip(grp, names):
                    e.book, e.why = n, level
                break
        else:
            for e in grp:
                e.book, e.why = f"{base} [{e.asin}]", "asin (fallback)"

    return entries


def extract_cover(m4b_path: Path, dest_no_ext: Path) -> Optional[Path]:
    try:
        t = MP4(m4b_path).tags or {}
    except Exception:  # noqa: BLE001
        return None
    if "covr" not in t:
        return None
    art = t["covr"][0]
    ext = ".png" if getattr(art, "imageformat", None) == 14 else ".jpg"
    out = dest_no_ext.with_suffix(ext)
    out.write_bytes(bytes(art))
    return out


def apply_plan(entries: list[PlanEntry], out_root: Path, pdf_dir: Optional[Path], write_cover: bool, copy: bool) -> list[str]:
    log: list[str] = []
    for e in entries:
        d = out_root / e.dir
        d.mkdir(parents=True, exist_ok=True)
        dest = d / e.m4b_name
        if dest.exists():
            log.append(f"SKIP (exists): {dest}")
            continue
        (shutil.copy2 if copy else shutil.move)(str(e.src), str(dest))

        if pdf_dir:
            pdf = pdf_dir / f"{e.asin}.pdf"
            if pdf.exists():
                shutil.copy2(str(pdf), str(d / e.pdf_name))

        if write_cover:
            extract_cover(dest, d / "cover")

        (d / "metadata.json").write_text(json.dumps(e.item, indent=1, ensure_ascii=False))
        log.append(f"{e.dir}/{e.m4b_name}")
    return log
