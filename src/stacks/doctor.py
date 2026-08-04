"""Preflight diagnostics: is this machine actually able to download + decrypt?

`stacks download` fails late and cryptically when a prerequisite is missing —
an ffmpeg build without Audible AAXC support, an auth file missing the device
identity the voucher key is derived from, an unwritable download dir. This
module checks each of those *before* a batch runs and reports every one, so
you fix them all in a single pass instead of one traceback at a time.

Every check returns a `Check` (name, status, detail, hint). The check
functions take their inputs as parameters (ffmpeg output, auth loader, paths,
clock) so they're unit-testable without a real ffmpeg, auth file, or network.
Only `run_checks` reaches out to the real environment.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import config

# Ordered worst-to-best; `worst_status` relies on this ranking.
STATUS_RANK = {"ok": 0, "skip": 1, "warn": 2, "fail": 3}

# The identity fields crypto.decrypt_voucher derives the AAXC key/iv from.
# Missing any of them means AAXC decryption can't work even with a valid login.
VOUCHER_IDENTITY_FIELDS = ("device_type", "device_serial_number", "user_id")

# Below this much free space on the download volume, warn — a single audiobook
# is tens to hundreds of MB and a batch is easily several GB.
LOW_DISK_BYTES = 1 * 1024**3


@dataclass
class Check:
    name: str
    status: str  # "ok" | "warn" | "fail" | "skip"
    detail: str
    hint: str = ""


# --------------------------------------------------------------- pure helpers


def parse_ffmpeg_version(version_output: str) -> Optional[str]:
    """Pull the version token out of `ffmpeg -version`'s first line.

    'ffmpeg version 6.1.1 Copyright ...' -> '6.1.1'
    'ffmpeg version n7.0-...' -> 'n7.0-...'  (distro/git builds vary; we just
    echo whatever token follows 'version' rather than trying to parse it)."""
    for line in (version_output or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "ffmpeg" and parts[1] == "version":
            return parts[2]
    return None


def ffmpeg_has_aaxc(mov_demuxer_help: str) -> bool:
    """True if this ffmpeg build exposes the Audible AAXC decrypt option.

    Audible support lives on the mov/mp4 demuxer as the `-audible_key` /
    `-audible_iv` / `-activation_bytes` options (NOT the separate 'aax'
    demuxer, which is CRI's unrelated game-audio format). Checking for the
    option string in `ffmpeg -h demuxer=mov` is the reliable probe."""
    return "audible_key" in (mov_demuxer_help or "")


def worst_status(checks: list[Check]) -> str:
    """The most severe status across all checks — drives the exit code."""
    if not checks:
        return "ok"
    return max((c.status for c in checks), key=lambda s: STATUS_RANK.get(s, 0))


def _staleness(mtime: float, now: float) -> str:
    days = max(0.0, (now - mtime) / 86400)
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    return f"{int(days)} days ago"


# ------------------------------------------------------------------- runners


def _capture(cmd: list[str]) -> str:
    """Run a command, returning combined stdout (empty string on any failure).
    ffmpeg prints `-h`/`-version` to stdout; we don't care about the rc."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except (OSError, ValueError):
        return ""
    return proc.stdout or ""


# ------------------------------------------------------------------- checks


def check_ffmpeg(
    which: Callable[[str], Optional[str]] = shutil.which,
    capture: Callable[[list[str]], str] = _capture,
) -> Check:
    path = which("ffmpeg")
    if not path:
        return Check(
            "ffmpeg", "fail", "not found on PATH",
            hint="install it — macOS: `brew install ffmpeg`, Debian/Ubuntu: `apt install ffmpeg`",
        )
    version = parse_ffmpeg_version(capture(["ffmpeg", "-version"])) or "unknown version"
    if not ffmpeg_has_aaxc(capture(["ffmpeg", "-hide_banner", "-h", "demuxer=mov"])):
        return Check(
            "ffmpeg", "warn", f"{version} at {path} — no Audible AAXC decrypt support",
            hint="this build lacks the `-audible_key` demuxer option; a standard ffmpeg from the last several years has it",
        )
    return Check("ffmpeg", "ok", f"{version} at {path}, AAXC decrypt supported")


def check_ffprobe(which: Callable[[str], Optional[str]] = shutil.which) -> Check:
    path = which("ffprobe")
    if not path:
        return Check(
            "ffprobe", "warn", "not found on PATH",
            hint="ships with ffmpeg — used for `stacks audit` and chapter verification; download still works without it",
        )
    return Check("ffprobe", "ok", f"found at {path}")


def check_auth(profile: str, loader: Callable[[Path], object], path: Path) -> Check:
    """Verify a usable auth file exists for `profile`, offline.

    `loader` loads an Authenticator from a path WITHOUT prompting for a
    password (so doctor never blocks on stdin). An encrypted file we can't
    open is reported honestly as un-inspectable rather than failed."""
    if not path.exists():
        return Check(
            f"auth ({profile})", "fail", "no saved login",
            hint="run `stacks auth login`, or `stacks auth import <auth.txt>` to adopt an existing file",
        )
    try:
        auth = loader(path)
    except Exception:  # noqa: BLE001 — any load failure means we can't inspect it here
        return Check(
            f"auth ({profile})", "warn", "present but encrypted — can't verify device identity without its password",
            hint="run `stacks auth status` (it will prompt) to confirm it's healthy",
        )

    device_info = getattr(auth, "device_info", None) or {}
    customer_info = getattr(auth, "customer_info", None) or {}
    have = {
        "device_type": device_info.get("device_type"),
        "device_serial_number": device_info.get("device_serial_number"),
        "user_id": customer_info.get("user_id"),
    }
    missing = [f for f in VOUCHER_IDENTITY_FIELDS if not have[f]]
    if missing:
        return Check(
            f"auth ({profile})", "warn",
            "signed in, but missing device identity: " + ", ".join(missing),
            hint="AAXC decryption derives its key from these — re-run `stacks auth login` to refresh the file",
        )

    name = customer_info.get("name") or customer_info.get("user_id") or "unknown"
    locale = getattr(getattr(auth, "locale", None), "country_code", "?")
    return Check(f"auth ({profile})", "ok", f"signed in as {name} ({locale})")


def check_library_cache(path: Path, now: Optional[float] = None,
                        loader: Callable[[Path], list] = None) -> Check:
    if not path.exists():
        return Check(
            "library cache", "warn", "not synced yet",
            hint="run `stacks library sync` (download will also fetch it on first use)",
        )
    now = time.time() if now is None else now
    try:
        if loader is None:
            import json
            items = json.loads(path.read_text()).get("items", [])
        else:
            items = loader(path)
    except Exception as e:  # noqa: BLE001
        return Check(
            "library cache", "warn", f"present but unreadable ({str(e)[:60]})",
            hint="re-run `stacks library sync` to rebuild it",
        )
    when = _staleness(path.stat().st_mtime, now)
    return Check("library cache", "ok", f"{len(items)} titles, synced {when}")


def check_download_dir(download_dir: str,
                      disk_usage: Callable[[str], object] = shutil.disk_usage) -> Check:
    """Confirm the configured download directory is writable and has room.

    Walks up to the nearest existing ancestor to measure free space, since the
    leaf dir may not exist yet (download creates it)."""
    from .utils import human_size

    target = Path(download_dir).expanduser()
    existing = target
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent

    if target.exists():
        probe = target / ".stacks-write-test"
        try:
            probe.touch()
            probe.unlink()
        except OSError:
            return Check(
                "download dir", "fail", f"{target} is not writable",
                hint="pick another with `--out` or set `download_dir` in settings.json",
            )

    try:
        free = disk_usage(str(existing)).free
    except OSError:
        return Check("download dir", "warn", f"{target} — couldn't measure free space")

    status, note = ("ok", "") if free >= LOW_DISK_BYTES else ("warn", " — low, a batch may not fit")
    return Check("download dir", status, f"{target}, {human_size(free)} free{note}")


def check_online(session_factory: Callable[[], object]) -> Check:
    """Live API probe: a single-item library call to confirm the token works.

    `session_factory()` is a context manager yielding (auth, client). Only run
    when the user opts in with `--online`, since it hits the network."""
    try:
        with session_factory() as (_, client):
            client.get("1.0/library", num_results=1, response_groups="product_desc")
    except Exception as e:  # noqa: BLE001
        return Check(
            "audible api", "fail", f"live request failed: {str(e)[:80]}",
            hint="your token may be expired — re-run `stacks auth login`",
        )
    return Check("audible api", "ok", "library request succeeded — auth token is live")


# --------------------------------------------------------------- orchestration


def run_checks(profile: str = "default", online: bool = False) -> list[Check]:
    """Run every offline check for `profile`; add the live API probe if asked."""
    import audible

    from .config import Settings
    from .session import open_session

    def _load_unlocked(path: Path):
        # No password argument -> raises on an encrypted file, which check_auth
        # deliberately catches rather than prompting.
        return audible.Authenticator.from_file(path)

    settings = Settings.load()
    checks = [
        check_ffmpeg(),
        check_ffprobe(),
        check_auth(profile, _load_unlocked, config.auth_file(profile)),
        check_library_cache(config.library_cache_file()),
        check_download_dir(settings.download_dir),
    ]
    if online:
        checks.append(check_online(lambda: open_session(profile)))
    return checks
