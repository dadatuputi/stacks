"""Stamp full Audible metadata + cover art onto an .m4b file.

Ported from a script that stamped 262 real files cleanly (title, author,
narrator, series, genres, dates, descriptions, ASIN/SKU/publisher as
freeform iTunes atoms, and upgraded cover art) — see README for the full
list of tags written.
"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

from .utils import strip_audible, strip_html

FF = "com.apple.iTunes"


@dataclass
class TagOptions:
    force: bool = False
    covers: bool = True
    force_covers: bool = False
    cover_size: int = 2400
    dry_run: bool = False


def _freeform(tags, key: str, value) -> None:
    if value not in (None, "", []):
        tags[f"----:{FF}:{key}"] = [MP4FreeForm(str(value).encode("utf-8"))]


def _image_size(data: bytes) -> Optional[tuple[int, int]]:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            m = data[i + 1]
            if m == 0xFF:
                i += 1
                continue
            if m in (0x01, 0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            seglen = int.from_bytes(data[i + 2 : i + 4], "big")
            if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                return (int.from_bytes(data[i + 7 : i + 9], "big"), int.from_bytes(data[i + 5 : i + 7], "big"))
            i += 2 + seglen
    return None


def _cover_candidates(item: dict, want: int) -> list[str]:
    imgs = item.get("product_images") or {}
    if not imgs:
        return []
    sizes = sorted((int(k) for k in imgs if str(k).isdigit()), reverse=True)
    if not sizes:
        return []
    import re

    urls, biggest = [], imgs[str(sizes[0])]
    if want > sizes[0] and re.search(r"_SL\d+_", biggest):
        urls.append(re.sub(r"_SL\d+_", f"_SL{want}_", biggest))
    urls += [imgs[str(s)] for s in sizes]
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _fetch(url: str, timeout: int = 30) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get_content_type()


def _best_cover(item: dict, want: int):
    best = None
    for url in _cover_candidates(item, want):
        try:
            data, ctype = _fetch(url)
        except Exception:  # noqa: BLE001
            continue
        dims = _image_size(data)
        if dims is None:
            continue
        if best is None or dims[0] > best[2][0]:
            best = (data, ctype, dims)
        if dims[0] >= want:
            break
    return best


def _pick_series(item: dict) -> dict:
    series = item.get("series") or []
    seq = [s for s in series if (s.get("sequence") or "").strip()]
    if seq:
        return seq[0]
    pub = item.get("publication_name")
    for s in series:
        if s.get("title") == pub:
            return s
    return series[0] if series else {}


def _genres(item: dict) -> list[str]:
    out = []
    for lad in item.get("category_ladders") or []:
        names = [n["name"] for n in lad.get("ladder") or []]
        if names:
            out.append(" / ".join(names))
    return out


def stamp(path: Path, item: dict, opts: TagOptions) -> list[str]:
    """Write every tag we can derive from `item` onto the .m4b at `path`.
    Returns a list of human-readable change descriptions."""
    f = MP4(path)
    if f.tags is None:
        f.add_tags()
    t = f.tags

    authors = [a["name"] for a in item.get("authors") or []]
    narrators = [n["name"] for n in item.get("narrators") or []]
    series = _pick_series(item)
    summary = strip_html(item.get("publisher_summary"))
    changed: list[str] = []

    def setif(key, value, label=None):
        if value in (None, "", []):
            return
        if opts.force or key not in t or t.get(key) != value:
            t[key] = value
            changed.append(label or key)

    clean_title = strip_audible(item["title"]) or item["title"]
    clean_sub = strip_audible(item.get("subtitle") or "")
    setif("\xa9nam", [clean_title], "title")
    if clean_sub:
        _freeform(t, "SUBTITLE", clean_sub)
    setif("\xa9ART", ["; ".join(authors)], "author")
    setif("aART", [authors[0]] if authors else None, "albumartist")
    setif("\xa9alb", [series.get("title") or clean_title], "album")
    setif("\xa9wrt", ["; ".join(narrators)] if narrators else None, "narrator")
    setif("\xa9gen", [_genres(item)[0]] if _genres(item) else ["Audiobook"], "genre")
    setif("stik", [2], "mediakind")
    t["pgap"] = True

    if d := (item.get("publication_datetime") or item.get("issue_date") or item.get("release_date")):
        setif("\xa9day", [d[:10]], "date")

    if summary:
        setif("desc", [summary[:255]], "desc")
        setif("ldes", [summary], "ldes")
    if merch := strip_html(item.get("merchandising_summary")):
        setif("\xa9cmt", [merch[:255]], "comment")
    if cr := item.get("copyright"):
        setif("cprt", [cr], "copyright")

    if seq := (series.get("sequence") or "").strip():
        try:
            val = [(int(float(seq)), 0)]
            if opts.force or t.get("trkn") != val:
                t["trkn"] = val
                changed.append("tracknum")
        except ValueError:
            pass

    _freeform(t, "ASIN", item["asin"])
    _freeform(t, "NARRATOR", "; ".join(narrators))
    _freeform(t, "PUBLISHER", item.get("publisher_name"))
    _freeform(t, "SERIES", series.get("title"))
    _freeform(t, "SERIES-PART", (series.get("sequence") or "").strip())
    _freeform(t, "LANGUAGE", item.get("language"))
    _freeform(t, "RUNTIME_MIN", item.get("runtime_length_min"))
    _freeform(t, "FORMAT", item.get("format_type"))
    _freeform(t, "SKU", item.get("sku"))
    _freeform(t, "GENRES", " | ".join(_genres(item)))
    _freeform(t, "PURCHASE_DATE", (item.get("purchase_date") or "")[:10])
    if r := (item.get("rating") or {}).get("overall_distribution", {}):
        _freeform(t, "RATING", r.get("display_average_rating"))
    if reviews := item.get("editorial_reviews"):
        _freeform(t, "EDITORIAL_REVIEW", strip_html(reviews[0])[:2000])

    want_cover = opts.covers and (opts.force or opts.force_covers or "covr" not in t)
    if want_cover:
        existing = _image_size(bytes(t["covr"][0])) if "covr" in t else None
        got = _best_cover(item, opts.cover_size)
        if got is None:
            changed.append("cover-UNAVAILABLE")
        else:
            data, ctype, (w, h) = got
            if existing and w <= existing[0] and not opts.force:
                changed.append(f"cover-kept({existing[0]}px, offered {w}px)")
            else:
                fmt = MP4Cover.FORMAT_PNG if "png" in ctype else MP4Cover.FORMAT_JPEG
                t["covr"] = [MP4Cover(data, imageformat=fmt)]
                was = f" was {existing[0]}px" if existing else ""
                changed.append(f"cover({w}x{h}, {len(data)//1024}kB{was})")

    if changed and not opts.dry_run:
        f.save()
    return changed
