"""Match local .m4b files that didn't come from `stacks download` (ripped
elsewhere, downloaded by another tool, etc.) back to your Audible library.

Matching order: ASIN tag -> ASIN in filename -> normalized title
(+duration when the title alone is ambiguous) -> title tokens/prefix with
duration agreement as a last resort. Every fallback beyond an exact ASIN or
unambiguous title is guarded by runtime agreement, because word-bag/prefix
matching alone is not safe.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mutagen.mp4 import MP4

from .utils import ASIN_IN_NAME, norm

FF = "com.apple.iTunes"
DUR_TOLERANCE_MIN = 3.0


@dataclass
class MatchResult:
    item: Optional[dict]
    how: str  # method name, or a human-readable reason for failure


def probe_duration_minutes(path: Path) -> Optional[float]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json", "-show_entries", "format=duration", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode:
        return None
    try:
        import json

        return float(json.loads(r.stdout)["format"]["duration"]) / 60.0
    except (KeyError, ValueError, __import__("json").JSONDecodeError):
        return None


def title_variants(item: dict) -> set[str]:
    from .utils import strip_audible

    t = (item.get("title") or "").strip()
    sub = (item.get("subtitle") or "").strip()
    out = {t}
    if sub:
        out |= {f"{t}: {sub}", f"{t} - {sub}", f"{t} {sub}"}
    for v in list(out):
        s = strip_audible(v)
        if s:
            out.add(s)
    return {v for v in out if v}


def build_index(items: list[dict]) -> tuple[dict, dict]:
    by_asin = {i["asin"]: i for i in items}
    by_title = defaultdict(list)
    for i in items:
        for v in title_variants(i):
            k = norm(v)
            if k and i not in by_title[k]:
                by_title[k].append(i)
    return by_asin, by_title


def existing_asin(tags) -> Optional[str]:
    v = (tags or {}).get(f"----:{FF}:ASIN")
    if v:
        raw = v[0]
        return (raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)).strip()
    return None


def match(path: Path, tags, minutes: Optional[float], by_asin: dict, by_title: dict) -> MatchResult:
    if a := existing_asin(tags):
        if a in by_asin:
            return MatchResult(by_asin[a], "asin-tag")

    if m := ASIN_IN_NAME.search(path.name):
        if m.group(1) in by_asin:
            return MatchResult(by_asin[m.group(1)], "asin-filename")
        return MatchResult(None, f"filename ASIN {m.group(1)} not in library")

    title = (tags or {}).get("\xa9nam", [""])[0]
    hits = by_title.get(norm(title), [])
    if not hits:
        return MatchResult(None, f"no title match for {title!r}")
    if len(hits) == 1:
        return MatchResult(hits[0], "title")

    if minutes is not None:
        close = [h for h in hits if h.get("runtime_length_min") and abs(h["runtime_length_min"] - minutes) <= DUR_TOLERANCE_MIN]
        if len(close) == 1:
            return MatchResult(close[0], "title+duration")
    return MatchResult(None, f"ambiguous ({len(hits)} candidates: {', '.join(h['asin'] for h in hits)})")


def match_tokens(title: str, minutes: Optional[float], by_title: dict) -> MatchResult:
    ft = set(norm(title).split())
    if len(ft) < 4 or minutes is None:
        return MatchResult(None, "no token candidate")
    cands = []
    for k, group in by_title.items():
        lt = set(k.split())
        if len(lt) < 4 or not (lt <= ft or ft <= lt):
            continue
        for it in group:
            rt = it.get("runtime_length_min")
            if rt and abs(rt - minutes) <= DUR_TOLERANCE_MIN and it not in cands:
                cands.append(it)
    if len(cands) == 1:
        return MatchResult(cands[0], "title-tokens+duration")
    if cands:
        return MatchResult(None, f"ambiguous tokens ({len(cands)}: {', '.join(c['asin'] for c in cands)})")
    return MatchResult(None, "no token candidate")


def match_prefix(title: str, minutes: Optional[float], by_title: dict) -> MatchResult:
    nt = norm(title)
    if len(nt) < 8 or minutes is None:
        return MatchResult(None, "no prefix candidate")
    cands = []
    for k, group in by_title.items():
        if len(k) < 8 or not (nt.startswith(k) or k.startswith(nt)):
            continue
        for it in group:
            rt = it.get("runtime_length_min")
            if rt and abs(rt - minutes) <= DUR_TOLERANCE_MIN and it not in cands:
                cands.append(it)
    if len(cands) == 1:
        return MatchResult(cands[0], "title-prefix+duration")
    if cands:
        return MatchResult(None, f"ambiguous prefix ({len(cands)}: {', '.join(c['asin'] for c in cands)})")
    return MatchResult(None, "no prefix candidate")


def match_file(path: Path, by_asin: dict, by_title: dict) -> MatchResult:
    try:
        tags = MP4(path).tags
    except Exception as e:  # noqa: BLE001
        return MatchResult(None, f"unreadable: {e}")

    minutes = probe_duration_minutes(path)
    result = match(path, tags, minutes, by_asin, by_title)
    if result.item is None and not ASIN_IN_NAME.search(path.name):
        title = (tags or {}).get("\xa9nam", [""])[0]
        for fallback in (match_prefix, match_tokens):
            fb = fallback(title, minutes, by_title)
            if fb.item is not None:
                return fb
            result = fb
    return result
