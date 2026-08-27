"""Audiobookshelf-compatible sidecar metadata.

Audiobookshelf treats a book folder's ``metadata.json`` as its ``absMetadata``
source, which sits *last* in ABS's default metadata precedence
(``folderStructure, audioMetatags, nfoFile, txtFiles, opfFile, absMetadata``)
and therefore wins over everything else. ABS also type-checks each key and
silently drops mismatches — a ``stringArray`` key holding objects validates
down to ``[]``, which is not "ignored" but "authoritatively empty".

So a raw Audible product dump written as ``metadata.json`` actively erases
authors and series in ABS. This module keeps the raw dump (under the
ABS-invisible name ``audible.json``) and additionally emits a real ABS
``metadata.json`` built from it.

Only the keys in ``ABS_KEYS`` are ever written, in the types ABS expects:

    string       title, subtitle, publishedYear, publishedDate, publisher,
                 description, isbn, asin, language
    stringArray  authors, narrators, series, genres, tags
    boolean      explicit, abridged
    array        chapters  ({start, end, title}, seconds)

References: ``server/utils/generators/abmetadataGenerator.js``,
``server/utils/parsers/parseSeriesString.js``.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Optional

from .utils import strip_html

#: Raw Audible product dump — deliberately *not* ``metadata.json``, so ABS
#: ignores it entirely.
RAW_FILENAME = "audible.json"
#: The ABS-schema sidecar.
ABS_FILENAME = "metadata.json"

ABS_KEYS = (
    "title", "subtitle", "authors", "narrators", "series", "genres", "tags",
    "publishedYear", "publishedDate", "publisher", "description", "isbn",
    "asin", "language", "explicit", "abridged", "chapters",
)

#: ABS parses a series sequence with ``/ #([^#\s]+)$/`` — the sequence is the
#: trailing token, introduced by a space and ``#``, with no whitespace and no
#: further ``#``. Anything else gets swallowed into the series *name*, so we
#: drop such a sequence rather than mis-name the series.
_SAFE_SEQUENCE = re.compile(r"^[^#\s]+$")

_DATE_KEYS = ("release_date", "issue_date", "publication_datetime")
_SUMMARY_KEYS = ("publisher_summary", "merchandising_summary", "extended_product_description")


def _text(value: Any) -> Optional[str]:
    """Unescaped, trimmed string — or None for anything that isn't one.
    Numbers are coerced (ABS coerces them too, but do it here so the file
    is honest about its own types)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None
    return html.unescape(value).strip() or None


def _bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "yes", "1"):
            return True
        if s in ("false", "no", "0"):
            return False
    return None


def _names(entries: Any) -> list[str]:
    """``[{'name': 'Brian Jacques', 'asin': ...}, ...]`` -> ``['Brian Jacques']``.
    Flat strings are accepted too. Order-preserving dedupe."""
    out: list[str] = []
    for e in entries or []:
        name = _text(e.get("name") if isinstance(e, dict) else e)
        if name and name not in out:
            out.append(name)
    return out


def _series(item: dict) -> list[str]:
    out: list[str] = []
    for s in item.get("series") or []:
        if isinstance(s, str):
            label = _text(s)
        elif isinstance(s, dict):
            title = _text(s.get("title") or s.get("name"))
            if not title:
                continue
            seq = _text(s.get("sequence"))
            label = f"{title} #{seq}" if seq and _SAFE_SEQUENCE.match(seq) else title
        else:
            continue
        if label and label not in out:
            out.append(label)
    return out


def _published(item: dict) -> tuple[Optional[str], Optional[str]]:
    """(publishedYear, publishedDate) — year is always a 4-char *string*."""
    for key in _DATE_KEYS:
        value = _text(item.get(key))
        if not value:
            continue
        date = value[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", value) else value
        year = value[:4] if re.match(r"^\d{4}", value) else None
        return year, date
    return None, None


def _language(item: dict) -> Optional[str]:
    """Audible ships ``"english"``; ABS displays what it is given."""
    lang = _text(item.get("language"))
    if not lang:
        return None
    return " ".join(w[:1].upper() + w[1:] for w in lang.split())


def _genres_and_tags(item: dict) -> tuple[list[str], list[str]]:
    """Flatten ``category_ladders[].ladder[].name`` in order, deduped: the
    first (broadest) category is the genre, the rest become tags."""
    names: list[str] = []
    for ladder in item.get("category_ladders") or []:
        rungs = ladder.get("ladder") if isinstance(ladder, dict) else None
        for rung in rungs or []:
            name = _text(rung.get("name") if isinstance(rung, dict) else rung)
            if name and name not in names:
                names.append(name)
    if not names:
        return [], []
    return names[:1], names[1:]


def _description(item: dict) -> Optional[str]:
    for key in _SUMMARY_KEYS:
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip():
            return strip_html(raw) or None
    return None


def _abridged(item: dict) -> Optional[bool]:
    fmt = _text(item.get("format_type"))
    if not fmt:
        return None
    return fmt.lower() != "unabridged"


def _seconds(chapter: dict, *keys: str) -> Optional[float]:
    for key in keys:
        v = chapter.get(key)
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            return float(v) / 1000 if key.endswith("_ms") else float(v)
    return None


def chapters_from_audible(payload: Any) -> list[dict]:
    """Turn Audible's chapter payload into ABS ``{start, end, title}`` dicts
    (seconds). Accepts the ``content_metadata`` dict returned by
    ``api.fetch_chapters``, a bare ``chapter_info`` dict, or a chapter list.
    Nested sub-chapters are flattened. Returns [] on anything unexpected —
    validation happens in :func:`clean_chapters`."""
    if isinstance(payload, dict):
        chapters = (payload.get("chapter_info") or payload).get("chapters")
    else:
        chapters = payload
    if not isinstance(chapters, list):
        return []

    flat: list[dict] = []

    def walk(entries: list) -> None:
        for ch in entries:
            if not isinstance(ch, dict):
                continue
            start = _seconds(ch, "start_offset_ms", "start_ms", "start_offset_sec", "start")
            length = _seconds(ch, "length_ms", "length_offset_ms", "length_sec", "length")
            end = _seconds(ch, "end_offset_ms", "end_ms", "end")
            if end is None and start is not None and length is not None:
                end = start + length
            title = _text(ch.get("title") or ch.get("chapter_title"))
            if start is not None and end is not None and title:
                # round to ms: the source values are integer milliseconds, and
                # float division would otherwise leave 54.977999999999994
                flat.append({"start": round(start, 3), "end": round(end, 3), "title": title})
            walk(ch.get("chapters") or [])

    walk(chapters)
    flat.sort(key=lambda c: c["start"])
    return flat


def clean_chapters(chapters: Any) -> Optional[list[dict]]:
    """All-or-nothing validation: ABS rejects the *entire* chapters array if
    any single entry is malformed, so return None rather than ship one."""
    if not isinstance(chapters, list) or not chapters:
        return None
    out: list[dict] = []
    for ch in chapters:
        if not isinstance(ch, dict):
            return None
        start, end = ch.get("start"), ch.get("end")
        title = ch.get("title")
        if isinstance(start, bool) or isinstance(end, bool):
            return None
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return None
        if not isinstance(title, str) or not title.strip():
            return None
        if start < 0 or end <= start:
            return None
        out.append({"start": float(start), "end": float(end), "title": title.strip()})
    out.sort(key=lambda c: c["start"])
    return out


def build_metadata(item: dict, chapters: Any = None) -> dict:
    """Map an Audible library/product ``item`` onto the ABS book schema.

    Every value is already in the type ABS expects, so nothing gets silently
    validated away. ``chapters`` is optional — omitted entirely when absent or
    invalid, in which case ABS falls back to the chapters embedded in the
    ``.m4b`` itself."""
    year, date = _published(item)
    genres, tags = _genres_and_tags(item)
    meta = {
        "title": _text(item.get("title")),
        "subtitle": _text(item.get("subtitle")),
        "authors": _names(item.get("authors")),
        "narrators": _names(item.get("narrators")),
        "series": _series(item),
        "genres": genres,
        "tags": tags,
        "publishedYear": year,
        "publishedDate": date,
        "publisher": _text(item.get("publisher_name")),
        "description": _description(item),
        "isbn": _text(item.get("isbn")),
        "asin": _text(item.get("asin")),
        "language": _language(item),
        "explicit": _bool(item.get("is_adult_product")),
        "abridged": _abridged(item),
    }
    cleaned = clean_chapters(chapters if isinstance(chapters, list) else chapters_from_audible(chapters)) if chapters else None
    if cleaned:
        meta["chapters"] = cleaned
    return meta


def _dump(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def write_sidecars(book_dir: Path, item: dict, chapters: Any = None) -> tuple[Path, Path]:
    """Write ``audible.json`` (raw dump) and ``metadata.json`` (ABS schema).

    Overwrites an existing ``metadata.json`` — which is exactly what migrates
    a folder written by an older stacks, where ``metadata.json`` *was* the raw
    dump and was blanking authors/series in Audiobookshelf."""
    raw = _dump(book_dir / RAW_FILENAME, item)
    abs_meta = _dump(book_dir / ABS_FILENAME, build_metadata(item, chapters))
    return raw, abs_meta
