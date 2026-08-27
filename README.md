# stacks

Download, decrypt, tag, and organize your Audible library from a single
command-line tool. stacks downloads books (individually or in batch), decrypts
them to plain `.m4b` files, stamps full metadata and cover art, and files them
into an `Author/Title/` library. It provides both an interactive menu and
scriptable subcommands.

```
   _____ __             __
  / ___// /_____ ______/ /_______
  \__ \/ __/ __ `/ ___/ //_/ ___/
 ___/ / /_/ /_/ / /__/ ,< (__  )
/____/\__/\__,_/\___/_/|_/____/

your Audible library, downloaded · decrypted · tagged · shelved
```

## Features

- **Download** — select books from a searchable checkbox picker, or pass
  ASINs, search terms, or `--all` on the command line. Downloads run
  concurrently with per-file progress bars.
- **Decrypt** — Audible delivers audio in an encrypted AAX/AAXC container.
  stacks requests a playback license for each book, derives the decryption key
  from your authenticated device, and remuxes to `.m4b` with ffmpeg
  stream-copy (no re-encoding). It works only on books your account owns.
- **Enrich** — stamps title, author, narrator, series, genres, dates,
  description, copyright, and cover art (up to 2400px), plus ASIN, SKU,
  publisher, and rating as freeform atoms. `stacks enrich` also tags files
  obtained elsewhere by matching them to your library on ASIN, filename,
  title, or title+runtime.
- **Organize** — lays files out as `Author/Author - Title/`, resolving
  same-author/same-title collisions with a narrator → year → ASIN ladder and
  merging author-name variants (`J.R.R. Tolkien` / `J. R. R. Tolkien`) through
  a reviewable alias file. Each book folder also gets an
  [Audiobookshelf](https://www.audiobookshelf.org/)-compatible `metadata.json`
  (see below) and the full Audible product record as `audible.json`.
- **Companion PDFs** — downloads the reference PDFs some titles ship alongside
  the audio, including owner-gated files.
- **Audit** — read-only health report for a folder of `.m4b` files: chapter
  coverage, cover art, and tag coverage.
- **Doctor** — `stacks doctor` preflight-checks ffmpeg AAXC support, login and
  device identity, library-cache freshness, and download-directory space.
  `--online` adds a live API call to confirm the token is valid.

Every capability is available as a scriptable subcommand; the interactive menu
is a layer on top.

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) on `PATH` (any recent build; AAX/AAXC demuxer
  support is required and has shipped in ffmpeg for years)

## Install

```bash
# ffmpeg
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian/Ubuntu

# stacks
git clone https://github.com/<you>/stacks.git
cd stacks
pipx install .               # or: pip install .
```

`pipx` is recommended so `stacks` runs in an isolated environment.

## Usage

Start the interactive menu:

```bash
stacks
```

The first run signs you in to Audible (email, password, and any 2FA/CAPTCHA),
then presents the menu: download, enrich, organize, fetch PDFs, audit, or run
doctor.

Scriptable subcommands:

```bash
stacks doctor                              # preflight checks
stacks auth login                          # one-time sign-in
stacks library sync                        # cache the library (also runs on first use)
stacks library list --search "sanderson"   # browse the cache

stacks download --all --out ~/Audiobooks   # download everything
stacks download B002V1O3XG B00ABCXYZ       # specific ASINs
stacks download "project hail mary"        # search terms

stacks enrich ~/Audiobooks                 # tag files from any source
stacks organize ~/Audiobooks --out ~/Library --apply
stacks metadata ~/Library                  # refresh Audiobookshelf sidecars in place
stacks pdfs
stacks audit ~/Library
```

Run `stacks <command> --help` for the options on any subcommand.

### Sign-in verification codes

If Audible's email or SMS verification code does not arrive, enable an
authenticator app under Amazon → Login & Security → Two-Step Verification and
sign in again. stacks then prompts for a time-based OTP code, which is
generated locally and does not depend on email or SMS delivery.

## Audiobookshelf

`stacks organize` writes two sidecar files into every book folder:

| File | Read by | Contents |
|---|---|---|
| `metadata.json` | Audiobookshelf (`absMetadata`) | Title, subtitle, authors, narrators, series (`"Redwall #2"`), genres, tags, published year/date, publisher, description, ISBN, ASIN, language, explicit, abridged |
| `audible.json` | nothing — informational | The complete Audible product record, every field, unmodified |

Audiobookshelf reads a book folder's `metadata.json` as its highest-priority
metadata source, above folder structure and embedded audio tags, and discards
any value whose type it does not expect. So the file has to be in ABS's own
schema: authors and narrators as plain strings, a series as `"Name #Sequence"`,
`publishedYear` as a string. The raw Audible record is not in that shape, which
is why it lives under a filename ABS ignores.

The `asin` field is included, so a later Quick Match in Audiobookshelf is an
exact lookup rather than a title guess.

Two details of that mapping are worth knowing:

- **`publishedYear` is the recording's release year, not the book's.** Audible's
  `release_date` records when the *audiobook* was published, so the 1986 Redwall
  novel carries its 2003 recording date. That is the right value for an
  audiobook library, but do not read it as the work's original publication year.
- **`abridged` is mapped from `format_type` explicitly**, never as "anything
  that isn't unabridged". Audible files lecture series, talks and audio-first
  works as `original_recording` (about 7% of a real library), and those are
  marked unabridged, not abridged. An unrecognized format is left unset rather
  than guessed at.

Both files are written by `stacks organize`, and by `stacks metadata <folder>`
for a library that is already laid out:

```bash
stacks metadata ~/Library    # refresh the sidecars in place, no files moved
```

> **Upgrading from stacks ≤ 0.1.0.** Older versions wrote the raw Audible dump
> *as* `metadata.json`. Audiobookshelf accepted it as authoritative and
> validated its mismatched field types down to empty, clearing the author and
> series on every scanned book, and dropping publisher, year and description
> with them. Renaming the file in a new version does not clean up copies
> already on disk: run `stacks metadata <library>` once over an existing
> library to overwrite them, then rescan in Audiobookshelf.

## Configuration

Files live under `~/.stacks` (override with `STACKS_HOME`):

| Path | Contents |
|---|---|
| `auth-<profile>.json` | Saved login, optionally password-encrypted at rest |
| `settings.json` | Defaults: download directory, quality, worker count, auto-enrich/organize |
| `cache/library.json` | Library metadata, refreshed by `stacks library sync` |
| `cache/chapters.json` | Per-book chapter data, cached on demand |
| `cache/pdfs/` | Downloaded companion PDFs |
| `activation_bytes` | Cached legacy-AAX activation bytes, when applicable |

Use multiple accounts with `stacks --profile <name> ...`.

## How decryption works

Audible's apps decrypt audio locally using a key derived from the device
identity and a per-book license issued to your account. stacks uses the same
license endpoint and key derivation through the
[`audible`](https://github.com/mkb79/Audible) package and ffmpeg's AAX/AAXC
demuxer. It works only on books your signed-in account owns.

Downloading DRM-protected content programmatically may conflict with Audible's
Terms of Service. stacks is intended for personal backups of purchased books.
Do not redistribute its output.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The test suite covers the pure logic (title/author matching, filename
sanitization, alias merging, folder-collision disambiguation, preflight
checks, and auth-prompt handling) with synthetic data. No network or ffmpeg is
required to run it.

## Acknowledgements

Built on [`audible`](https://github.com/mkb79/Audible) (API client and auth),
[`mutagen`](https://mutagen.readthedocs.io/) (MP4 tagging), and ffmpeg
(decrypt/remux). The licensing and AAXC voucher request shapes follow
[`audible-cli`](https://github.com/mkb79/audible-cli).

## License

MIT — see [LICENSE](LICENSE).
