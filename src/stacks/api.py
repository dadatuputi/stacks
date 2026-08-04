"""Thin wrapper around the Audible API: library, chapters, licensing, PDFs.

The library/chapters fetching here is ported near-verbatim from a script
that was run against a real 262-book account and worked cleanly. The
licensing/download-resolution code follows the same request shapes
audible-cli uses (see module docstring in crypto.py for why that's not a
DRM "crack" — it's the documented authenticated-device flow).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import audible
import httpx

from . import config
from .crypto import decrypt_voucher

LIBRARY_GROUPS = [
    "badge_types", "category_ladders", "claim_code_url", "contributors",
    "customer_rights", "is_downloaded", "is_finished", "is_returnable",
    "listening_status", "media", "order_details", "origin_asin", "pdf_url",
    "percent_complete", "price", "product_attrs", "product_desc",
    "product_details", "product_extended_attrs", "product_plan_details",
    "product_plans", "provided_review", "rating", "relationships",
    "review_attrs", "reviews", "series",
]
IMAGE_SIZES = "252,315,360,408,500,558,570,882,1024,2400"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ApiError(RuntimeError):
    pass


def probe_groups(client: audible.Client, groups: list[str]) -> list[str]:
    """Drop any response group the API rejects — one bad name fails the whole request."""
    good = []
    for g in groups:
        try:
            client.get("1.0/library", num_results=1, response_groups=g)
            good.append(g)
        except Exception:
            pass
        time.sleep(0.2)
    return good


def fetch_library(client: audible.Client, refresh: bool = False, progress_cb=None) -> list[dict]:
    """Fetch (or load-from-cache) the full library with every useful
    response group. Cached to disk so repeated CLI runs are instant."""
    cache = config.library_cache_file()
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())["items"]

    groups = probe_groups(client, LIBRARY_GROUPS)
    items: list[dict] = []
    page = 1
    while True:
        r = client.get(
            "1.0/library",
            num_results=1000,
            page=page,
            response_groups=", ".join(groups),
            image_sizes=IMAGE_SIZES,
            sort_by="-PurchaseDate",
        )
        batch = r.get("items", [])
        items += batch
        if progress_cb:
            progress_cb(len(items))
        if len(batch) < 1000:
            break
        page += 1

    seen, out = set(), []
    for it in items:
        if it["asin"] not in seen:
            seen.add(it["asin"])
            out.append(it)

    cache.write_text(json.dumps({"items": out, "response_groups": groups}, indent=1, ensure_ascii=False))
    return out


def fetch_chapters(client: audible.Client, asin: str, cache: Optional[dict] = None) -> dict:
    cache = cache if cache is not None else _load_chapters_cache()
    if asin in cache:
        return cache[asin]
    r = client.get(
        f"1.0/content/{asin}/metadata",
        response_groups="chapter_info, content_reference, always-returned",
        quality="High",
    )
    data = r.get("content_metadata", {})
    cache[asin] = data
    return data


def _load_chapters_cache() -> dict:
    p = config.chapters_cache_file()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_chapters_cache(cache: dict) -> None:
    config.chapters_cache_file().write_text(json.dumps(cache, indent=1, ensure_ascii=False))


# --------------------------------------------------------------- licensing


@dataclass
class DownloadTarget:
    asin: str
    url: str
    codec: str                      # "AAX" | "AAXC" | other
    key: Optional[str] = None       # hex, AAXC only
    iv: Optional[str] = None        # hex, AAXC only
    activation_bytes: Optional[str] = None  # AAX only


def get_license(client: audible.Client, asin: str, quality: str = "high") -> dict:
    body = {
        "supported_drm_types": ["Mpeg", "Adrm"],
        "quality": "High" if quality in ("high", "best") else "Normal",
        "consumption_type": "Download",
        "response_groups": "last_position_heard, pdf_url, content_reference, chapter_info",
    }
    try:
        return client.post(f"content/{asin}/licenserequest", body=body)
    except Exception as e:  # noqa: BLE001
        raise ApiError(f"license request failed for {asin}: {e}") from e


def _dig(d: dict, *paths: tuple[str, ...]) -> Any:
    """Try several key-paths against a dict; return the first that resolves."""
    for path in paths:
        cur = d
        try:
            for k in path:
                cur = cur[k]
            if cur:
                return cur
        except (KeyError, TypeError):
            continue
    return None


def resolve_download(auth: audible.Authenticator, client: audible.Client, asin: str, quality: str = "high") -> DownloadTarget:
    """Ask Audible how to get the actual audio bytes for this ASIN, and
    return everything needed to fetch + decrypt it."""
    lr = get_license(client, asin, quality=quality)
    content_license = lr.get("content_license") or {}
    metadata = content_license.get("content_metadata") or {}

    codec = _dig(metadata, ("content_reference", "content_format")) or "AAXC"
    url = _dig(
        metadata,
        ("content_url", "offline_url"),
        ("content_reference", "content_url", "offline_url"),
        ("content_reference", "content_url"),
    )
    if not url:
        raise ApiError(
            f"couldn't find a download URL in the license response for {asin} "
            f"(codec reported as {codec!r}) — Audible may have changed its API shape"
        )

    if codec.upper().startswith("AAX") and codec.upper() != "AAXC":
        # legacy format: needs the account's activation bytes, not a voucher
        ab = auth.get_activation_bytes(filename=config.activation_bytes_file())
        return DownloadTarget(asin=asin, url=str(url), codec=codec, activation_bytes=ab)

    if codec.upper() == "AAXC":
        voucher = decrypt_voucher(auth, lr)
        return DownloadTarget(asin=asin, url=str(url), codec=codec, key=voucher.get("key"), iv=voucher.get("iv"))

    # DRM-free (rare, but some publishers opt out) — just a plain download.
    return DownloadTarget(asin=asin, url=str(url), codec=codec)


# --------------------------------------------------------------- companion PDFs


def fetch_companion_pdf(auth: audible.Authenticator, item: dict, dest: Path) -> tuple[bool, str]:
    """Download a book's companion PDF. Audible serves some PDFs publicly
    (pre_purchase_docs) and others only to authenticated owners
    (post_purchase_docs, 403s without website cookies) — this tries the
    owner-gated route first since it's the superset."""
    if dest.exists():
        return True, "already on disk"

    pdf_url = item.get("pdf_url")
    if not pdf_url:
        return False, "no pdf_url for this title"

    domain = getattr(auth.locale, "domain", "com")
    cookies = {k: v.replace('"', "") for k, v in (auth.website_cookies or {}).items()}
    headers = {"User-Agent": BROWSER_UA, "Accept": "application/pdf,application/octet-stream,*/*"}

    candidates = []
    if cookies:
        candidates.append(f"https://www.audible.{domain}/companion-file/{item['asin']}")
    candidates.append(pdf_url)

    last_err = "no candidates worked"
    for url in candidates:
        try:
            with httpx.Client(cookies=cookies, headers=headers, follow_redirects=True, timeout=60) as c:
                r = c.get(url)
                r.raise_for_status()
                if not r.content.startswith(b"%PDF-"):
                    last_err = f"response from {httpx.URL(url).host} wasn't a PDF"
                    continue
                tmp = dest.with_suffix(".pdf.part")
                tmp.write_bytes(r.content)
                tmp.replace(dest)
                return True, "downloaded"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:120]
    return False, last_err
