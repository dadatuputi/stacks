import json
from pathlib import Path

from stacks import doctor
from stacks.doctor import Check


# --------------------------------------------------------------- pure helpers


def test_parse_ffmpeg_version():
    assert doctor.parse_ffmpeg_version("ffmpeg version 6.1.1 Copyright (c) 2000 devs") == "6.1.1"
    assert doctor.parse_ffmpeg_version("ffmpeg version n7.0-11-gabc Copyright") == "n7.0-11-gabc"
    assert doctor.parse_ffmpeg_version("garbage") is None
    assert doctor.parse_ffmpeg_version("") is None


def test_ffmpeg_has_aaxc():
    assert doctor.ffmpeg_has_aaxc("  -audible_key <binary>  AES-128 Key for Audible AAXC files")
    assert not doctor.ffmpeg_has_aaxc("  -activation_bytes <binary>  ...")  # AAX only, no AAXC
    assert not doctor.ffmpeg_has_aaxc("")


def test_worst_status_ranks_fail_over_warn_over_ok():
    assert doctor.worst_status([]) == "ok"
    assert doctor.worst_status([Check("a", "ok", ""), Check("b", "ok", "")]) == "ok"
    assert doctor.worst_status([Check("a", "ok", ""), Check("b", "warn", "")]) == "warn"
    assert doctor.worst_status([Check("a", "warn", ""), Check("b", "fail", "")]) == "fail"
    assert doctor.worst_status([Check("a", "skip", ""), Check("b", "ok", "")]) == "skip"


# --------------------------------------------------------------------- ffmpeg


def test_check_ffmpeg_missing():
    c = doctor.check_ffmpeg(which=lambda _: None)
    assert c.status == "fail"
    assert "PATH" in c.detail and c.hint


def test_check_ffmpeg_present_with_aaxc():
    def capture(cmd):
        if "-version" in cmd:
            return "ffmpeg version 6.1.1 Copyright"
        return "  -audible_key <binary> AES-128 Key for Audible AAXC files"

    c = doctor.check_ffmpeg(which=lambda _: "/usr/bin/ffmpeg", capture=capture)
    assert c.status == "ok"
    assert "6.1.1" in c.detail


def test_check_ffmpeg_present_without_aaxc_warns():
    def capture(cmd):
        return "ffmpeg version 3.0 Copyright" if "-version" in cmd else "no audible options here"

    c = doctor.check_ffmpeg(which=lambda _: "/usr/bin/ffmpeg", capture=capture)
    assert c.status == "warn"
    assert "AAXC" in c.detail


def test_check_ffprobe_missing_is_only_a_warning():
    assert doctor.check_ffprobe(which=lambda _: None).status == "warn"
    assert doctor.check_ffprobe(which=lambda _: "/usr/bin/ffprobe").status == "ok"


# ----------------------------------------------------------------------- auth


class _FakeAuth:
    def __init__(self, device=None, customer=None, country="us"):
        self.device_info = device
        self.customer_info = customer
        self.locale = type("L", (), {"country_code": country})()


def test_check_auth_no_file_fails(tmp_path):
    c = doctor.check_auth("default", lambda p: None, tmp_path / "nope.json")
    assert c.status == "fail"


def test_check_auth_encrypted_is_warn_not_fail(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("{}")

    def loader(_):
        raise ValueError("encrypted")

    c = doctor.check_auth("default", loader, path)
    assert c.status == "warn"
    assert "encrypted" in c.detail.lower()


def test_check_auth_missing_identity_warns(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("{}")
    auth = _FakeAuth(device={"device_type": "A2CZ"}, customer={})  # no serial, no user_id
    c = doctor.check_auth("default", lambda _: auth, path)
    assert c.status == "warn"
    assert "device_serial_number" in c.detail and "user_id" in c.detail


def test_check_auth_healthy_is_ok(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("{}")
    auth = _FakeAuth(
        device={"device_type": "A2CZ", "device_serial_number": "SERIAL123"},
        customer={"user_id": "amzn1.account.ABC", "name": "Ada Lovelace"},
    )
    c = doctor.check_auth("default", lambda _: auth, path)
    assert c.status == "ok"
    assert "Ada Lovelace" in c.detail and "us" in c.detail


# -------------------------------------------------------------- library cache


def test_check_library_cache_absent_warns(tmp_path):
    c = doctor.check_library_cache(tmp_path / "library.json")
    assert c.status == "warn"


def test_check_library_cache_counts_titles_and_dates(tmp_path):
    path = tmp_path / "library.json"
    path.write_text(json.dumps({"items": [{"asin": "B1"}, {"asin": "B2"}]}))
    mtime = path.stat().st_mtime
    c = doctor.check_library_cache(path, now=mtime + 3 * 86400)  # 3 days later
    assert c.status == "ok"
    assert "2 titles" in c.detail and "3 days ago" in c.detail


def test_check_library_cache_corrupt_warns(tmp_path):
    path = tmp_path / "library.json"
    path.write_text("{not json")
    assert doctor.check_library_cache(path).status == "warn"


# --------------------------------------------------------------- download dir


def _usage(free):
    return type("U", (), {"free": free, "total": 0, "used": 0})()


def test_check_download_dir_writable_with_room(tmp_path):
    c = doctor.check_download_dir(str(tmp_path), disk_usage=lambda _: _usage(50 * 1024**3))
    assert c.status == "ok"


def test_check_download_dir_low_space_warns(tmp_path):
    c = doctor.check_download_dir(str(tmp_path), disk_usage=lambda _: _usage(100 * 1024**2))
    assert c.status == "warn"
    assert "low" in c.detail


def test_check_download_dir_nonexistent_leaf_measures_ancestor(tmp_path):
    target = tmp_path / "does" / "not" / "exist"
    c = doctor.check_download_dir(str(target), disk_usage=lambda _: _usage(50 * 1024**3))
    assert c.status == "ok"  # ancestor exists and is measurable


# ---------------------------------------------------------------------- online


def test_check_online_success():
    class _Client:
        def __enter__(self):
            return (None, self)

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return {"items": []}

    c = doctor.check_online(lambda: _Client())
    assert c.status == "ok"


def test_check_online_failure_reports_fail():
    class _Boom:
        def __enter__(self):
            raise RuntimeError("token expired")

        def __exit__(self, *a):
            return False

    c = doctor.check_online(lambda: _Boom())
    assert c.status == "fail"
    assert "token expired" in c.detail
