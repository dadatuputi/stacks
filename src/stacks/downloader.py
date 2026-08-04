"""Download, decrypt, and remux audiobooks into playable .m4b files.

Flow per book:
    1. license request  -> DownloadTarget (url + codec + key/iv or activation_bytes)
    2. stream the encrypted file to a temp path, with a live progress bar
    3. ffmpeg remuxes + decrypts in one shot (stream copy — no re-encode,
       so this is fast and lossless) into the final .m4b
    4. if the container came out with zero named chapters, best-effort
       inject titles fetched from Audible's chapter API
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import audible
import httpx

from . import api
from .utils import sanitize

BROWSER_UA = api.BROWSER_UA


class DownloadError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise DownloadError(
            "ffmpeg is not on your PATH. Install it first "
            "(macOS: `brew install ffmpeg`, Debian/Ubuntu: `apt install ffmpeg`) "
            "— stacks needs an ffmpeg build with AAX/AAXC demuxer support "
            "(any ffmpeg from the last few years has this)."
        )


@dataclass
class DownloadResult:
    asin: str
    title: str
    path: Optional[Path]
    ok: bool
    error: Optional[str] = None


def _dest_filename(item: dict) -> str:
    author = sanitize((item.get("authors") or [{}])[0].get("name") or "Unknown Author", 60)
    title = sanitize(item.get("title") or item["asin"], 90)
    return f"{author} - {title}.m4b"


def _stream_to_file(url: str, dest: Path, on_progress: Optional[Callable[[int, int], None]] = None) -> None:
    headers = {"User-Agent": BROWSER_UA}
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as c:
        with c.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            done = 0
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
            tmp.replace(dest)


def _run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise DownloadError(f"ffmpeg failed: {proc.stderr.strip()[-800:]}")


def _decrypt_and_remux(target: api.DownloadTarget, raw_path: Path, out_path: Path) -> None:
    if target.codec.upper() == "AAXC":
        if not (target.key and target.iv):
            raise DownloadError("AAXC download but no decryption key/iv resolved")
        _run_ffmpeg(["-audible_key", target.key, "-audible_iv", target.iv, "-i", str(raw_path), "-c", "copy", "-f", "mp4", str(out_path)])
    elif target.codec.upper().startswith("AAX"):
        if not target.activation_bytes:
            raise DownloadError("AAX download but no activation_bytes resolved")
        _run_ffmpeg(["-activation_bytes", str(target.activation_bytes), "-i", str(raw_path), "-c", "copy", "-f", "mp4", str(out_path)])
    else:
        # DRM-free fallback: normalize into an m4b container, re-encoding
        # only if the source audio codec isn't already AAC/ALAC.
        try:
            _run_ffmpeg(["-i", str(raw_path), "-c", "copy", "-f", "mp4", str(out_path)])
        except DownloadError:
            _run_ffmpeg(["-i", str(raw_path), "-c:a", "aac", "-b:a", "128k", "-f", "mp4", str(out_path)])


def _has_titled_chapters(path: Path) -> bool:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-of", "json", "-show_chapters", str(path)],
            capture_output=True, text=True,
        )
        import json as _json
        chapters = _json.loads(proc.stdout).get("chapters", [])
        return bool(chapters) and all((c.get("tags") or {}).get("title") for c in chapters)
    except Exception:  # noqa: BLE001
        return True  # don't block on a probe failure


def _chapter_field(ch: dict, *names: str, default=None):
    for n in names:
        if n in ch and ch[n] is not None:
            return ch[n]
    return default


def _inject_chapters(path: Path, chapter_info: dict) -> bool:
    """Best-effort: if the remuxed file has no titled chapters, pull titles
    from Audible's chapter API and mux them in. Never raises — a missing or
    oddly-shaped chapter payload just means we leave the file as-is."""
    try:
        chapters = (chapter_info or {}).get("chapters") or []
        if not chapters:
            return False
        lines = [";FFMETADATA1"]
        for ch in chapters:
            start_ms = _chapter_field(ch, "start_offset_ms", "start_ms", default=0)
            length_ms = _chapter_field(ch, "length_ms", "length_offset_ms", default=0)
            title = _chapter_field(ch, "title", "chapter_title", default="Chapter")
            end_ms = start_ms + length_ms
            lines += [
                "[CHAPTER]", "TIMEBASE=1/1000", f"START={start_ms}", f"END={end_ms}",
                f"title={title}",
            ]
        meta_text = "\n".join(lines) + "\n"

        with tempfile.TemporaryDirectory() as td:
            meta_path = Path(td) / "chapters.ffmeta"
            meta_path.write_text(meta_text, encoding="utf-8")
            fixed = Path(td) / "fixed.m4b"
            _run_ffmpeg([
                "-i", str(path), "-i", str(meta_path),
                "-map_metadata", "1", "-map_chapters", "1",
                "-c", "copy", "-f", "mp4", str(fixed),
            ])
            shutil.copy2(fixed, path)
        return True
    except Exception:  # noqa: BLE001
        return False


def download_one(
    auth: audible.Authenticator,
    client: audible.Client,
    item: dict,
    dest_dir: Path,
    quality: str = "high",
    fix_chapters: bool = True,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> DownloadResult:
    asin = item["asin"]
    title = item.get("title", asin)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / _dest_filename(item)

    if out_path.exists() and out_path.stat().st_size > 0:
        return DownloadResult(asin=asin, title=title, path=out_path, ok=True, error="already downloaded")

    try:
        target = api.resolve_download(auth, client, asin, quality=quality)
        with tempfile.TemporaryDirectory() as td:
            raw_ext = ".aaxc" if target.codec.upper() == "AAXC" else ".aax"
            raw_path = Path(td) / f"{asin}{raw_ext}"
            _stream_to_file(target.url, raw_path, on_progress=on_progress)
            tmp_out = Path(td) / out_path.name
            _decrypt_and_remux(target, raw_path, tmp_out)

            if fix_chapters and not _has_titled_chapters(tmp_out):
                try:
                    chapters = api.fetch_chapters(client, asin)
                    _inject_chapters(tmp_out, chapters)
                except Exception:  # noqa: BLE001
                    pass  # untitled/no chapters beats a failed download

            shutil.move(str(tmp_out), str(out_path))
        return DownloadResult(asin=asin, title=title, path=out_path, ok=True)
    except Exception as e:  # noqa: BLE001
        return DownloadResult(asin=asin, title=title, path=None, ok=False, error=str(e)[:300])


def download_many(
    auth: audible.Authenticator,
    client: audible.Client,
    items: list[dict],
    dest_dir: Path,
    quality: str = "high",
    workers: int = 4,
    fix_chapters: bool = True,
    on_task_progress: Optional[Callable[[str, int, int], None]] = None,
    on_task_done: Optional[Callable[[DownloadResult], None]] = None,
) -> list[DownloadResult]:
    """Download several books concurrently. `on_task_progress(asin, done, total)`
    and `on_task_done(result)` are called from worker threads — keep them
    fast and thread-safe (a rich Progress object is fine)."""
    require_ffmpeg()
    results: list[DownloadResult] = []

    def _work(item: dict) -> DownloadResult:
        asin = item["asin"]
        cb = (lambda done, total: on_task_progress(asin, done, total)) if on_task_progress else None
        return download_one(auth, client, item, dest_dir, quality=quality, fix_chapters=fix_chapters, on_progress=cb)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = {ex.submit(_work, item): item for item in items}
        for fut in as_completed(futures):
            result = fut.result()
            results.append(result)
            if on_task_done:
                on_task_done(result)
    return results
