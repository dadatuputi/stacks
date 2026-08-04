# CLAUDE.md — working notes for whoever (human or agent) picks this up next

This file is for you, not for end users — see README.md for that. It exists
so a fresh Claude Code session (or a human skimming the repo) doesn't have
to re-derive project state from scratch.

## What this is

`stacks` is a pip-installable CLI (`src/` layout, `pyproject.toml`, console
entry point `stacks`) that downloads audiobooks from Audible, decrypts them
(AAX/AAXC via ffmpeg's native demuxer support), stamps full metadata + cover
art, and organizes them into `Author/Title/` folders. It has both an
interactive menu (`stacks` with no args) and scriptable subcommands.

## Module map

| File | Responsibility |
|---|---|
| `config.py` | Paths under `~/.stacks` (or `$STACKS_HOME`), `Settings` dataclass. No network. |
| `auth.py` | Interactive login wizard, auth-file import, status/logout. Wraps `audible.Authenticator`. |
| `session.py` | One-liner context manager: load auth -> open `audible.Client`. |
| `api.py` | Library fetch (paginated, cached), chapter fetch, license-request → `DownloadTarget` resolution, companion-PDF fetch (cookie-gated + public fallback). |
| `crypto.py` | AAXC voucher decrypt (device-identity-derived AES key, reimplemented rather than importing `audible`'s private `aescipher` module). |
| `downloader.py` | Stream-download the encrypted file, ffmpeg decrypt+remux to `.m4b`, best-effort chapter injection, concurrent batch orchestration. |
| `tagger.py` | Stamps an Audible library `item` dict onto an `.m4b`'s MP4 atoms + cover art. Pure — no matching, no network beyond cover image fetch. |
| `matcher.py` | Fuzzy-matches an arbitrary local `.m4b` back to a library item (ASIN tag → filename → title → title+duration → tokens/prefix+duration). Used by `enrich` for files that didn't come from `stacks download`. |
| `organizer.py` | Folder-layout planning with narrator→year→ASIN disambiguation, author-alias clustering/suggestion. |
| `audit.py` | Read-only ffprobe/mutagen report on a folder of `.m4b`s. |
| `catalog.py` | Presentation-layer search/labeling over the cached library, used by both `cli.py` and `interactive.py`. |
| `ui.py` | Rich theme, banner, table/progress-bar factories — all the "snazzy" visuals live here. |
| `interactive.py` | The menu-driven flow. Two functions (`pick_books`, `run_downloads`) are deliberately public because `cli.py`'s `download` command reuses them when invoked without explicit ASINs. |
| `cli.py` | Typer app; every subcommand is a thin wrapper calling into the modules above. |

`matcher.py`/`tagger.py`/`organizer.py`/`audit.py` are near-verbatim ports of
four standalone scripts that were run against a real 262-book Audible
library in a prior session and worked cleanly (100% chapter/cover/tag
coverage, correct disambiguation, correct alias merging). That logic is
proven. Tests in `tests/` cover it with synthetic data.

## What is NOT yet verified

`api.resolve_download` + `crypto.decrypt_voucher` + `downloader._decrypt_and_remux`
— the actual license-request → AAXC-decrypt → ffmpeg-remux path — has never
completed a real end-to-end run. It's built from:

- the `audible` Python package's documented method signatures (`from_login`,
  `get_activation_bytes`, `Authenticator.device_info`/`customer_info`)
- `audible-cli`'s known request shapes for `content/{asin}/licenserequest`
  and AAXC voucher decryption (device_type+serial+customer_id+asin →
  SHA256 → AES-CBC key/iv)
- ffmpeg's documented `-audible_key`/`-audible_iv` flags for its AAXC demuxer

...but every environment available to the agent that wrote this (a
cloud sandbox, and a "device_bash" automation VM reached via a desktop
bridge) sits behind a network allowlist that blocks both PyPI and
Audible's API — so `pip install` and any live API call fail with
`blocked-by-allowlist` before ever reaching Audible. **If you're Claude
Code running directly on the user's own machine, you likely don't have
that restriction — you may be able to complete the test the prior agent
couldn't.**

### The test to run

```bash
pipx install .        # or: pip install -e ".[dev]" in a venv
stacks auth import /path/to/existing/auth.txt --no-encrypt   # or `stacks auth login`
stacks library sync
stacks download "Rikki Tikki Tavi"    # ASIN B0098OONQG, ~35 min — shortest title in this account's library, fast to iterate on
```

If it fails, the traceback will point at one of three places, roughly in
order of likelihood:

1. **`api.resolve_download`'s `_dig()` calls** — the exact key path to the
   download URL in the license response (`content_url.offline_url` vs
   `content_reference.content_url.offline_url`) was reconstructed from a
   summarized (not verbatim) read of `audible-cli`'s source, so the nesting
   may be slightly off for the current API version. Fix: add a debug dump
   of the raw `licenserequest` JSON on `ApiError`, inspect it, adjust the
   `_dig` paths.
2. **`crypto.decrypt_voucher`** — if the key/iv derivation is wrong, ffmpeg
   will fail to decrypt with a clear error, or produce corrupt/silent
   audio. Cross-check against `audible`'s actual installed
   `audible.aescipher` module in the working environment if this happens —
   it's the ground truth this was reimplemented from.
3. **ffmpeg version** — `-audible_key`/`-audible_iv` need a build with AAXC
   demuxer support (anything reasonably recent). `ffmpeg -version` first.

Once a real download succeeds, update this section (or just delete it) —
don't leave stale "unverified" warnings in a repo that's since verified.

## Running tests

```bash
pip install -e ".[dev]"
pytest -q          # 24 tests, all pure-function, no network/ffmpeg required
python -m pyflakes src/stacks/*.py   # should be silent
```

## Things a next pass might reasonably improve

- No CI secrets/mechanism for testing the live download path automatically
  (by nature — it needs a real Audible account). The GitHub Actions
  workflow only runs the offline test suite + `--help` smoke checks.
- `downloader._inject_chapters` is best-effort and only fires when ffmpeg's
  native chapter parsing comes up empty/untitled; it hasn't been exercised
  against a real chapters.json response shape (field names like
  `start_offset_ms` vs `start_ms` are guessed defensively with `_chapter_field`
  fallbacks, not confirmed).
- No `stacks doctor`/preflight command that checks ffmpeg version, auth
  validity, and disk space before a batch download — would be a nice
  addition once the core path is confirmed working.
