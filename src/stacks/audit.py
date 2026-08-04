"""Report what's actually inside a folder of .m4b files: chapters, cover
art, and tag coverage. Read-only — never modifies anything."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mutagen.mp4 import MP4

KNOWN = [
    ("\xa9nam", "title"), ("\xa9ART", "artist"), ("aART", "albumartist"),
    ("\xa9alb", "album"), ("\xa9wrt", "narrator/composer"), ("\xa9day", "date"),
    ("\xa9gen", "genre"), ("\xa9cmt", "comment"), ("desc", "desc"),
    ("ldes", "longdesc"), ("cprt", "copyright"), ("stik", "mediakind"),
    ("trkn", "tracknum"), ("covr", "cover"), ("\xa9too", "encoder"),
]


def _ffprobe(p: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json", "-show_format", "-show_streams", "-show_chapters", str(p)],
        capture_output=True, text=True,
    )
    if r.returncode:
        return {"_error": r.stderr.strip()[:200]}
    return json.loads(r.stdout)


def inspect(p: Path) -> dict:
    out = {"file": p.name, "size": p.stat().st_size}
    d = _ffprobe(p)
    if "_error" in d:
        out["error"] = d["_error"]
        return out

    chapters = d.get("chapters") or []
    out["chapters"] = len(chapters)
    out["chapter_titles"] = sum(1 for c in chapters if (c.get("tags") or {}).get("title"))
    if chapters:
        out["first_chapter"] = (chapters[0].get("tags") or {}).get("title")

    streams = d.get("streams") or []
    out["audio"] = [s["codec_name"] for s in streams if s["codec_type"] == "audio"]
    out["video"] = [s["codec_name"] for s in streams if s["codec_type"] == "video"]
    out["duration_h"] = round(float(d["format"].get("duration", 0)) / 3600, 2)

    try:
        t = MP4(p).tags or {}
    except Exception as e:  # noqa: BLE001
        out["mutagen_error"] = str(e)
        return out

    present, missing = [], []
    for atom, label in KNOWN:
        (present if atom in t else missing).append(label)
    out["present"], out["missing"] = present, missing
    out["freeform"] = sorted(k.split(":")[-1] for k in t if k.startswith("----"))
    out["other_atoms"] = sorted(k for k in t if k not in dict(KNOWN) and not k.startswith("----"))
    if "covr" in t:
        out["cover_bytes"] = len(bytes(t["covr"][0]))
    return out


def audit_dir(directory: Path, workers: int = 6) -> list[dict]:
    files = sorted(directory.glob("*.m4b"))
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(inspect, files))


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if "error" not in r]
    errs = [r for r in rows if "error" in r]
    with_ch = [r for r in ok if r.get("chapters")]
    no_ch = [r["file"] for r in ok if not r.get("chapters")]
    covers = [r for r in ok if "cover_bytes" in r]

    tally = Counter()
    for r in ok:
        tally.update(r.get("present", []))
    ff = Counter()
    for r in ok:
        ff.update(r.get("freeform", []))

    return {
        "n": len(rows),
        "errors": [(r["file"], r["error"]) for r in errs],
        "with_chapters": len(with_ch),
        "no_chapters": no_ch,
        "fully_titled_chapters": sum(1 for r in with_ch if r["chapter_titles"] == r["chapters"]),
        "covers": len(covers),
        "cover_sizes_kb": sorted(r["cover_bytes"] // 1024 for r in covers),
        "tag_coverage": {label: tally.get(label, 0) for _, label in KNOWN},
        "freeform_coverage": dict(ff),
        "total_files": len(ok),
    }
