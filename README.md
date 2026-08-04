# stacks

A snazzy, interactive CLI for your Audible library: download books (single or
in batch), decrypt them into plain `.m4b` files, stamp them with full
metadata and cover art, and file them away into a tidy `Author/Title/`
library — all from one command.

```
   _____ __             __
  / ___// /_____ ______/ /_______
  \__ \/ __/ __ `/ ___/ //_/ ___/
 ___/ / /_/ /_/ / /__/ ,< (__  )
/____/\__/\__,_/\___/_/|_/____/

your Audible library, downloaded · decrypted · tagged · shelved
```

## What it does

- **Download** — pick books from a searchable, checkbox-driven picker (or
  pass ASINs/search terms on the command line, or `--all`), and stacks
  downloads them concurrently with live per-file progress bars.
- **Decrypt** — Audible ships audio in an encrypted `AAX`/`AAXC` container.
  stacks requests a license for each book the same way the official app
  does, derives the decryption key from your own authenticated device
  (nothing is "cracked" — this is the documented playback path for content
  you already own), and remuxes straight to a plain `.m4b` via `ffmpeg`
  stream-copy. No re-encoding, no quality loss, seconds per book.
- **Enrich** — stamps title, author, narrator, series, genres, dates,
  descriptions, copyright, and 2400px cover art onto every file, plus
  ASIN/SKU/publisher/rating as iTunes-style freeform atoms. Works on books
  downloaded elsewhere too — `stacks enrich` fuzzy-matches existing `.m4b`
  files back to your library by ASIN, filename, title, or title+runtime.
- **Organize** — lays files out as `Author/Author - Title/`, resolving
  same-author/same-title collisions with a narrator → year → ASIN
  disambiguation ladder, and merges author-name spelling variants
  (`J.R.R. Tolkien` / `J. R. R. Tolkien`) via a reviewable alias file.
- **Companion PDFs** — fetches the reference PDFs some audiobooks ship
  alongside the audio (maps, charts, slides), including the ones gated
  behind account ownership.
- **Audit** — a read-only health report on a folder of `.m4b` files:
  chapter coverage, cover art presence/size, tag coverage.

Everything above is also a scriptable, flag-driven subcommand — the
interactive menu is a convenience layer on top, not the only way in.

## Install

Requires Python 3.10+ and [ffmpeg](https://ffmpeg.org/) on your `PATH`
(any reasonably recent build — the AAX/AAXC decrypt support has been in
ffmpeg for years).

```bash
# ffmpeg, if you don't have it
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian/Ubuntu

# clone and install
git clone https://github.com/<you>/stacks.git
cd stacks
pipx install .               # or: pip install .
```

`pipx` is recommended so `stacks` gets its own isolated environment.

## Quickstart

```bash
stacks              # interactive menu — start here
```

First run walks you through signing in to Audible (email, password, and
whatever 2FA/CAPTCHA your account requires), then drops you into a menu:
download books, enrich files you already have, organize a folder, fetch
companion PDFs, or audit what's on disk.

Everything is also scriptable:

```bash
stacks auth login                              # one-time sign-in
stacks library sync                            # cache your library (auto-runs on first use too)
stacks library list --search "sanderson"       # browse the cache

stacks download --all --out ~/Audiobooks       # download everything
stacks download B002V1O3XG B00ABCXYZ           # download specific ASINs
stacks download "project hail mary"            # or search terms

stacks enrich ~/Audiobooks                     # tag files from any source
stacks organize ~/Audiobooks --out ~/Library --apply
stacks pdfs
stacks audit ~/Library
```

Run `stacks <command> --help` for the full flag list on any of these.

## Configuration

Everything lives under `~/.stacks` (override with `STACKS_HOME`):

| Path | What |
|---|---|
| `auth-<profile>.json` | Saved login (optionally password-encrypted at rest) |
| `settings.json` | Your defaults — download dir, quality, worker count, auto-enrich/organize |
| `cache/library.json` | Your library metadata, refreshed via `stacks library sync` |
| `cache/chapters.json` | Per-book chapter data, fetched lazily and cached |
| `cache/pdfs/` | Downloaded companion PDFs |
| `activation_bytes` | Cached legacy-AAX activation bytes, if your account needs them |

Multiple Audible accounts: `stacks --profile work download ...`.

## How the decrypt step works (and why it's not sketchy)

Audible's official apps decrypt audio locally using a key derived from your
authenticated device identity and a per-book license Audible issues to
*your* account. `stacks` follows that exact same request flow — same
license endpoint, same key derivation — via the well-documented
[`audible`](https://github.com/mkb79/Audible) Python package and `ffmpeg`'s
native AAX/AAXC demuxer support. It only works against books your signed-in
account already owns; there's no secret being defeated, just the normal
playback path run from a terminal instead of an app. That said, downloading
DRM-wrapped content programmatically may still brush up against Audible's
Terms of Service depending on how you use it — this tool is for making
personal backups of books you've bought, for your own devices. Don't
redistribute what it produces.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The test suite covers the pure logic (title/author matching, filename
sanitization, alias merging, folder-collision disambiguation) with
synthetic data — no network or ffmpeg required to run it.

## Acknowledgements

Built on [`audible`](https://github.com/mkb79/Audible) for the Audible API
client/auth, [`mutagen`](https://mutagen.readthedocs.io/) for MP4 tagging,
and `ffmpeg` for the actual decrypt/remux. The request shapes for
licensing and AAXC voucher decryption follow the same approach as
[`audible-cli`](https://github.com/mkb79/audible-cli).

## License

MIT — see [LICENSE](LICENSE).
