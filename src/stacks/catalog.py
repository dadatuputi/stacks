"""Presentation-layer helpers for browsing a cached library: search,
labels for pickers, and row formatting for tables."""

from __future__ import annotations

from . import config
from .utils import human_duration, norm

import json


def load_cached_items() -> list[dict]:
    p = config.library_cache_file()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())["items"]
    except (json.JSONDecodeError, KeyError, OSError):
        return []


def author_of(item: dict) -> str:
    authors = item.get("authors") or []
    return authors[0]["name"] if authors else "Unknown"


def series_of(item: dict) -> str:
    series = item.get("series") or []
    if not series:
        return ""
    s = series[0]
    seq = f" #{s['sequence']}" if s.get("sequence") else ""
    return f"{s.get('title', '')}{seq}"


def year_of(item: dict) -> str:
    d = item.get("publication_datetime") or item.get("issue_date") or item.get("release_date") or ""
    return d[:4]


def runtime_of(item: dict) -> str:
    mins = item.get("runtime_length_min")
    return human_duration(mins * 60) if mins else ""


def search_items(items: list[dict], query: str) -> list[dict]:
    if not query.strip():
        return items
    q = norm(query)
    terms = q.split()

    def score(item: dict) -> int:
        haystack = norm(f"{item.get('title', '')} {author_of(item)} {series_of(item)}")
        return sum(1 for t in terms if t in haystack)

    hits = [(score(i), i) for i in items]
    hits = [(s, i) for s, i in hits if s == len(terms)]
    hits.sort(key=lambda si: (-si[0], si[1].get("title", "")))
    return [i for _, i in hits]


def label_for(item: dict) -> str:
    year = year_of(item)
    series = series_of(item)
    bits = [author_of(item), "—", item.get("title", item["asin"])]
    if series:
        bits.append(f"({series})")
    if year:
        bits.append(f"[{year}]")
    return " ".join(bits)


def table_row(item: dict) -> tuple[str, ...]:
    return (author_of(item), item.get("title", ""), series_of(item), year_of(item), runtime_of(item), item["asin"])
