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
| `organizer.py` | Folder-layout planning with narrator→year→ASIN disambiguation, author-alias clustering/suggestion. Writes the per-book sidecars via `absmeta`. |
| `absmeta.py` | Maps an Audible product dict onto Audiobookshelf's `metadata.json` schema, and writes it plus the raw dump as `audible.json`. Pure, no network. |
| `audit.py` | Read-only ffprobe/mutagen report on a folder of `.m4b`s. |
| `doctor.py` | Preflight diagnostics (ffmpeg+AAXC support, auth/device-identity, library cache, download-dir writability/space, optional live API probe). Pure/injectable check functions, testable without a real environment. |
| `catalog.py` | Presentation-layer search/labeling over the cached library, used by both `cli.py` and `interactive.py`. |
| `ui.py` | Rich theme, banner, table/progress-bar factories — all the "snazzy" visuals live here. |
| `interactive.py` | The menu-driven flow. Two functions (`pick_books`, `run_downloads`) are deliberately public because `cli.py`'s `download` command reuses them when invoked without explicit ASINs. |
| `cli.py` | Typer app; every subcommand is a thin wrapper calling into the modules above. |

`matcher.py`/`tagger.py`/`organizer.py`/`audit.py` are near-verbatim ports of
four standalone scripts that were run against a real 262-book Audible
library in a prior session and worked cleanly (100% chapter/cover/tag
coverage, correct disambiguation, correct alias merging). That logic is
proven. Tests in `tests/` cover it with synthetic data.

## Download path — VERIFIED (2026-08-03)

The full license-request → AAXC-decrypt → ffmpeg-remux path has now completed
real end-to-end runs against a live account (3 books: Cricket in Times Square,
Alien: Out of the Shadows, Ponzi Supernova). Each produced a valid `.m4b` that
decodes to clean stereo narration (mean ≈ −21 dB), with titled chapters and
embedded cover art. Two real bugs surfaced and were fixed during that run:

1. **Download UA (`downloader._stream_to_file`).** The CloudFront audio
   endpoint 403s a browser User-Agent; it only serves the file to an
   Audible-app UA. Fixed by streaming with `api.AUDIBLE_UA`
   (`Audible/671 CFNetwork/...`). The website/PDF routes still use
   `api.BROWSER_UA` — don't unify them.
2. **AAXC vs AAX detection (`api.resolve_download`).** Modern Audible serves
   **AAXC** (voucher-encrypted) even when the license reports
   `content_format: "AAX_22_64"` and `drm_type: "Adrm"` — the downloaded file's
   brand is still `aaxc` and ffmpeg needs `-audible_key`/`-audible_iv`, not
   `-activation_bytes`. Feeding `-activation_bytes` to an AAXC file does NOT
   error — ffmpeg silently stream-copies the still-encrypted audio, yielding a
   valid-looking container whose audio decodes to ~27-channel garbage. Detection
   is now by voucher presence (`content_license.license_response`), not the
   format string. `crypto.decrypt_voucher` is confirmed correct (32-hex-char
   key + iv, accepted by ffmpeg's aaxc demuxer).

Regression-check a suspect decrypt by **decoding**, not probing: a bad decrypt
passes `ffprobe` (container metadata is intact) but `ffmpeg -i file -t 10 -f
null -` throws `channel element not allocated` / decodes as 27 channels.

Legacy true-AAX (voucher-less, `activation_bytes`) and DRM-free branches exist
but haven't been exercised against a real file — no AAX-only titles were in the
test account.

### Fast iteration title

`stacks download "Ponzi Supernova"` (ASIN B06Y4G67WB, free, ~72 MB) is the
quickest real download to test with.

## Running tests

```bash
pip install -e ".[dev]"
pytest -q          # 24 tests, all pure-function, no network/ffmpeg required
python -m pyflakes src/stacks/*.py   # should be silent
```

## Audiobookshelf sidecars (2026-08-27)

`organizer.apply_plan` used to dump the raw Audible product dict (~156 keys)
into each book folder as `metadata.json`. That name is Audiobookshelf's
`absMetadata` source, which is **last in ABS's default precedence and therefore
highest priority** — above folder structure and embedded audio tags. ABS
type-checks each key and silently drops mismatches, and its `stringArray`
validator filters out non-strings, so `authors: [{name: ...}]` validated down
to `[]` — ABS authoritatively setting every book's authors and series to empty.
Reported against a real 261-book library.

Now: the raw dump goes to `audible.json` (a name ABS ignores) and `absmeta.py`
builds a real ABS-schema `metadata.json` alongside it. Things to keep in mind
when touching `absmeta.py`:

- Only keys in `ABS_KEYS` may be written, in ABS's expected types — a wrong
  type is worse than a missing key, because ABS overwrites with the empty
  validated value rather than falling through to the next source.
- A series sequence is parsed by ABS with `/ #([^#\s]+)$/`. A sequence
  containing whitespace or a second `#` would be swallowed into the series
  *name*, so `_series` drops the sequence rather than mis-name the series.
- Chapters are all-or-nothing in ABS (one bad entry rejects the whole array),
  hence `clean_chapters` returning None instead of a partial list. Nothing
  currently passes chapters — the `.m4b`'s embedded chapters already reach ABS
  through `audioMetatags` — but `chapters_from_audible` is there for the
  `api.fetch_chapters` payload if a caller wants them.
- Migration: renaming doesn't clean up files already on disk, so
  `stacks metadata <library>` (menu: 🧾) rewrites the sidecars for an existing
  tree, and `apply_plan` refreshes them even on its `SKIP (exists)` path.

## Things a next pass might reasonably improve

- No CI secrets/mechanism for testing the live download path automatically
  (by nature — it needs a real Audible account). The GitHub Actions
  workflow only runs the offline test suite + `--help` smoke checks.
- `downloader._inject_chapters` is best-effort and only fires when ffmpeg's
  native chapter parsing comes up empty/untitled; it hasn't been exercised
  against a real chapters.json response shape (field names like
  `start_offset_ms` vs `start_ms` are guessed defensively with `_chapter_field`
  fallbacks, not confirmed).
- ~~No `stacks doctor`/preflight command~~ — done. `stacks doctor` (and the
  🩺 menu entry) checks ffmpeg presence + **AAXC decrypt support** (probes
  `ffmpeg -h demuxer=mov` for `-audible_key`, the accurate signal — note the
  standalone `aax` demuxer ffmpeg lists is CRI's unrelated format), ffprobe,
  auth file + the three device-identity fields the voucher key derives from,
  library-cache freshness, and download-dir writability/free space. `--online`
  adds a live single-item library call to confirm the token isn't expired.
  Exit code is nonzero only on a hard `fail`. It notably catches the gap
  `downloader.require_ffmpeg()` misses: an ffmpeg that exists but can't do AAXC.
