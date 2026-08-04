"""Text-normalization helpers shared by the matcher, tagger, and organizer.

Ported and consolidated from a working set of one-off scripts that were
battle-tested against a real 262-book Audible library, so the regexes here
are opinionated on purpose (they're tuned to what Audible's title/author
strings actually look like, not a general-purpose "clean any string" tool).
"""

from __future__ import annotations

import html
import re
import unicodedata

FF_MEAN = "com.apple.iTunes"  # freeform atom mean-string used throughout

AUDIBLE_BOILERPLATE = re.compile(
    r"\s*[:\-–—]?\s*\b(an?\s+)?audible\s+originals?\b"
    r"(\s+(drama|production|series|audio\s+drama))?\s*",
    re.I,
)

HONORIFICS = re.compile(
    r"\b(ph\s*\.?\s*d|m\s*\.?\s*d|d\s*\.?\s*o|ed\s*\.?\s*d|j\s*\.?\s*d|"
    r"psy\s*\.?\s*d|dr|prof|professor|sir|dame|rev|md|phd|mba|lcsw|mft|rn)\b\.?",
    re.I,
)
ROLE_SUFFIX = re.compile(
    r"\s*[-–—]\s*(introduction|foreword|afterword|preface|translator|"
    r"translated\s+by|editor|edited\s+by|narrator|contributor|adapted\s+by)\b.*$",
    re.I,
)

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$|^\d{10}$")
ASIN_IN_NAME = re.compile(r"_([A-Z0-9]{10}|\d{10})\.m4b$")


def strip_audible(s: str | None) -> str | None:
    """Remove 'An Audible Original[ Drama]' boilerplate. May return ''."""
    if not s:
        return s
    out = AUDIBLE_BOILERPLATE.sub(" ", s)
    return re.sub(r"\s+", " ", out).strip(" :–—-")


def norm(s: str | None) -> str:
    """Aggressively normalize a title/author string for fuzzy matching:
    strip accents, lowercase, drop 'unabridged', collapse punctuation."""
    s = unicodedata.normalize("NFKD", html.unescape(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"\b(un)?abridged\b|\bunabr\b|\bwith ebook\b", " ", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def strip_html(s: str | None) -> str:
    s = re.sub(r"<(br|/p|/div)\s*/?>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(s)).strip()


def sanitize(s: str | None, maxlen: int = 90) -> str:
    """Filesystem-safe path component: strips Audible boilerplate, swaps
    problem characters, collapses whitespace, truncates politely."""
    s = unicodedata.normalize("NFC", html.unescape(s or ""))
    s = strip_audible(s) or s
    s = s.replace("/", " & ").replace("\\", " & ")
    s = re.sub(r'[?"\x00-\x1f]', "", s)
    s = re.sub(r"\s*:\s*", " - ", s)
    s = re.sub(r"[*<>|]", "-", s)
    s = re.sub(r"\s*-\s*-\s*", " - ", s)
    s = re.sub(r"(?<=\w)-\s+", " - ", s)
    s = re.sub(r"\s+", " ", s).strip(" .-")
    if len(s) > maxlen:
        s = s[:maxlen].rstrip(" ,;:-")
    return s or "Unknown"


def name_parts(name: str | None) -> tuple[str, list[str]]:
    """(surname, [given tokens]) with roles, honorifics and accents removed."""
    s = html.unescape(name or "")
    s = ROLE_SUFFIX.sub("", s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = HONORIFICS.sub(" ", s)
    toks = [t for t in re.split(r"[^A-Za-z]+", s) if t]
    if not toks:
        return "", []
    return toks[-1].lower(), [t.lower() for t in toks[:-1]]


def _edit_le1(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    for i in range(len(b)):
        if a == b[:i] + b[i + 1 :]:
            return True
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) <= 1


def same_person(n1: str, n2: str) -> bool:
    """Conservative: surname must match AND given names must be compatible —
    identical, one a pure-initial form of the other, or a one-character typo."""
    s1, g1 = name_parts(n1)
    s2, g2 = name_parts(n2)
    if not s1 or s1 != s2:
        return False
    if not g1 or not g2:
        return False
    if [x[0] for x in g1] != [x[0] for x in g2]:
        return False
    if g1 == g2:
        return True
    pure1 = all(len(x) == 1 for x in g1)
    pure2 = all(len(x) == 1 for x in g2)
    if pure1 or pure2:
        return True
    return all(_edit_le1(a, b) for a, b in zip(g1, g2))


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:3.1f}{unit}" if unit != "B" else f"{int(n)}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"
