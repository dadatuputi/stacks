"""The `stacks` command-line entry point.

Run with no subcommand for the interactive menu; every capability is also
available as a scriptable flag-driven subcommand for use in cron jobs,
Makefiles, or your own automation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import typer

from . import api, audit as audit_mod, auth as auth_mod, catalog, doctor as doctor_mod, matcher, organizer, tagger
from .config import Settings, pdf_cache_dir
from .session import open_session
from .ui import console, error, info, make_table, panel, step_progress, success, warn

app = typer.Typer(add_completion=False, no_args_is_help=False, help="Download, decrypt, tag, and organize your Audible library.")
auth_app = typer.Typer(help="Manage your saved Audible login.")
library_app = typer.Typer(help="Browse/refresh your cached library.")
app.add_typer(auth_app, name="auth")
app.add_typer(library_app, name="library")

_profile_option = typer.Option("default", "--profile", help="Named auth profile, for multiple Audible accounts.")


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context, profile: str = _profile_option):
    ctx.obj = {"profile": profile}
    if ctx.invoked_subcommand is None:
        from . import interactive

        interactive.run(profile=profile)
        raise typer.Exit()


# --------------------------------------------------------------------- auth


@auth_app.command("login")
def auth_login(ctx: typer.Context, locale: Optional[str] = typer.Option(None, help="us, uk, de, fr, ca, au, in, it, es, jp, br")):
    profile = ctx.obj["profile"]
    try:
        auth_mod.login(profile, locale=locale)
    except auth_mod.AuthError as e:
        error(str(e))
        raise typer.Exit(1)


@auth_app.command("import")
def auth_import(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="Existing audible-format auth file (e.g. auth.txt from audible-cli or another audible-package script)."),
    encrypt: bool = typer.Option(True, help="Password-protect the copy stacks saves."),
):
    """Adopt an existing Audible auth file instead of logging in again."""
    profile = ctx.obj["profile"]
    if not path.exists():
        error(f"{path} not found")
        raise typer.Exit(1)
    import getpass as _getpass

    dest_password = _getpass.getpass("Choose a password to protect the imported auth file: ") if encrypt else None
    try:
        auth_mod.import_file(path, profile, dest_password=dest_password)
    except auth_mod.AuthError as e:
        error(str(e))
        raise typer.Exit(1)
    success(f"imported {path} into profile '{profile}'")


@auth_app.command("status")
def auth_status(ctx: typer.Context):
    s = auth_mod.status(ctx.obj["profile"])
    table = make_table("Account", ["Field", "Value"])
    for k, v in s.items():
        table.add_row(k, str(v))
    console.print(table)


@auth_app.command("logout")
def auth_logout(
    ctx: typer.Context,
    keep_device: bool = typer.Option(False, "--keep-device", help="Only delete the local file; leave the device registered with Amazon."),
):
    removed, note = auth_mod.logout(ctx.obj["profile"], deregister=not keep_device)
    (success if removed else info)(note)


@auth_app.command("deregister-all")
def auth_deregister_all(ctx: typer.Context):
    """Deregister EVERY device on the account (including the Audible app on your
    phone) to clear a pile-up of stale registrations causing API 403s."""
    if not typer.confirm("This signs out ALL your Audible devices, everywhere. Continue?"):
        raise typer.Exit()
    try:
        note = auth_mod.deregister_all(ctx.obj["profile"])
    except auth_mod.AuthError as e:
        error(str(e))
        raise typer.Exit(1)
    success(note)


# ------------------------------------------------------------------ library


@library_app.command("sync")
def library_sync(ctx: typer.Context):
    with open_session(ctx.obj["profile"]) as (auth, client):
        with console.status("Fetching your Audible library...", spinner="dots12"):
            items = api.fetch_library(client, refresh=True)
    success(f"cached {len(items)} titles")


@library_app.command("list")
def library_list(ctx: typer.Context, search: str = typer.Option("", "--search", "-s"), limit: int = 50):
    items = catalog.load_cached_items()
    if not items:
        warn("no cached library — run `stacks library sync` first")
        raise typer.Exit(1)
    matches = catalog.search_items(items, search)
    table = make_table(f"Library ({len(matches)} match{'es' if len(matches) != 1 else ''})", ["Author", "Title", "Series", "Year", "Runtime", "ASIN"])
    for item in matches[:limit]:
        table.add_row(*catalog.table_row(item))
    console.print(table)
    if len(matches) > limit:
        info(f"... and {len(matches) - limit} more (raise --limit to see them)")


# ------------------------------------------------------------------ download


@app.command()
def download(
    ctx: typer.Context,
    query: List[str] = typer.Argument(None, help="ASINs or search terms. Omit for the interactive picker."),
    all_: bool = typer.Option(False, "--all", help="Download your entire library."),
    out: Optional[Path] = typer.Option(None, "--out", "-o"),
    quality: str = typer.Option("high", help="high | normal"),
    workers: int = typer.Option(4, "--workers", "-w"),
    enrich: bool = typer.Option(True, help="Stamp metadata + cover art after downloading."),
    organize: bool = typer.Option(False, help="Move into Author/Title folders after downloading."),
    pdfs: bool = typer.Option(True, help="Fetch companion PDFs where available."),
):
    """Download one, many, or all audiobooks in your library."""
    settings = Settings.load()
    items = catalog.load_cached_items()
    if not items:
        with open_session(ctx.obj["profile"]) as (_, client):
            with console.status("First run — fetching your library...", spinner="dots12"):
                items = api.fetch_library(client)

    if all_:
        picked = items
    elif query:
        by_asin = {i["asin"]: i for i in items}
        picked = []
        for q in query:
            if q in by_asin:
                picked.append(by_asin[q])
            else:
                hits = catalog.search_items(items, q)
                if not hits:
                    warn(f"no match for {q!r}")
                elif len(hits) > 1:
                    warn(f"{q!r} matched {len(hits)} titles — using the closest, be more specific to pick another:")
                    picked.append(hits[0])
                else:
                    picked.append(hits[0])
    else:
        from . import interactive

        picked = interactive.pick_books(items)

    if not picked:
        warn("nothing to download")
        raise typer.Exit(1)

    dest = out or Path(settings.download_dir)
    dest.mkdir(parents=True, exist_ok=True)
    panel(f"[bold]{len(picked)}[/bold] book(s) → [bold]{dest}[/bold]  (quality={quality}, workers={workers})", title="Downloading")

    with open_session(ctx.obj["profile"]) as (auth, client):
        from . import interactive

        interactive.run_downloads(auth, client, picked, dest, quality, workers, enrich, organize, pdfs)


# -------------------------------------------------------------------- enrich


@app.command()
def enrich(
    path: Path = typer.Argument(..., help="Folder of .m4b files to match + tag."),
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, help="Overwrite tags that already have a value."),
    covers: bool = typer.Option(True, help="Fetch/upgrade cover art."),
    force_covers: bool = typer.Option(False, help="Replace existing covers even if not smaller."),
    cover_size: int = typer.Option(2400),
):
    """Match local .m4b files to your Audible library and stamp full metadata."""
    items = catalog.load_cached_items()
    if not items:
        error("no cached library — run `stacks library sync` first")
        raise typer.Exit(1)
    by_asin, by_title = matcher.build_index(items)
    files = sorted(path.glob("*.m4b"))
    if not files:
        warn(f"no .m4b files in {path}")
        raise typer.Exit(1)

    opts = tagger.TagOptions(force=force, covers=covers, force_covers=force_covers, cover_size=cover_size, dry_run=dry_run)
    matched, unmatched = 0, []
    table = make_table("Enrichment plan" if dry_run else "Enrichment results", ["File", "Match", "Changes"])
    with step_progress("Matching + tagging") as p:
        t = p.add_task("enrich", total=len(files))
        for f in files:
            result = matcher.match_file(f, by_asin, by_title)
            if result.item is None:
                unmatched.append((f, result.how))
                table.add_row(f.name[:50], "no match", result.how[:40])
            else:
                matched += 1
                changed = tagger.stamp(f, result.item, opts)
                table.add_row(f.name[:50], result.how, ", ".join(changed)[:60] or "-")
            p.advance(t)
    console.print(table)
    success(f"{matched}/{len(files)} matched")
    if unmatched:
        warn(f"{len(unmatched)} unmatched:")
        for f, why in unmatched:
            console.print(f"    {f.name}: {why}")


# ------------------------------------------------------------------ organize


@app.command()
def organize(
    path: Path = typer.Argument(..., help="Folder of already-tagged .m4b files."),
    out: Path = typer.Option(Path("Library"), "--out", "-o"),
    aliases_file: Optional[Path] = typer.Option(None, "--aliases"),
    suggest_aliases: bool = typer.Option(False, help="Just write author_aliases.suggested.json and exit."),
    apply_: bool = typer.Option(False, "--apply", help="Actually move/copy files. Without this, only a plan is printed."),
    copy: bool = typer.Option(False, help="Copy instead of move."),
    cover_file: bool = typer.Option(True, help="Extract a cover.jpg/png alongside each book."),
):
    """Lay out .m4b files as Author/Author - Title/, resolving collisions."""
    items = catalog.load_cached_items()
    if not items:
        error("no cached library — run `stacks library sync` first")
        raise typer.Exit(1)

    if suggest_aliases:
        suggested = organizer.suggest_aliases(items)
        dest = path / "author_aliases.suggested.json"
        dest.write_text(json.dumps(suggested, indent=1, ensure_ascii=False))
        success(f"wrote {len(suggested)} proposed merges to {dest}")
        return

    by_asin = {i["asin"]: i for i in items}
    aliases = organizer.load_aliases(aliases_file or (path / "author_aliases.json"))

    files, unmatched = {}, []
    for p in sorted(path.glob("*.m4b")):
        asin = organizer.file_asin(p)
        if asin and asin in by_asin:
            files[p] = asin
        else:
            unmatched.append(p)
    if unmatched:
        warn(f"{len(unmatched)} file(s) have no ASIN match (run `stacks enrich` first): " + ", ".join(p.name for p in unmatched[:5]))
    if not files:
        error("nothing to organize")
        raise typer.Exit(1)

    entries = organizer.plan(by_asin, files, aliases)
    table = make_table("Organize plan", ["Destination", "Source file"])
    for e in sorted(entries, key=lambda e: str(e.dir)):
        table.add_row(f"{e.dir}/{e.m4b_name}", e.src.name[:50])
    console.print(table)

    if not apply_:
        info("dry run — pass --apply to actually move files")
        return
    organizer.apply_plan(entries, out, pdf_cache_dir(), write_cover=cover_file, copy=copy)
    success(f"organized {len(entries)} book(s) into {out}")


# ---------------------------------------------------------------------- pdfs


@app.command()
def pdfs(ctx: typer.Context, out: Optional[Path] = typer.Option(None, "--out", "-o", help="Defaults to the stacks cache dir.")):
    """Fetch companion PDFs for every title in your library that has one."""
    items = catalog.load_cached_items()
    if not items:
        error("no cached library — run `stacks library sync` first")
        raise typer.Exit(1)
    have_pdf = [i for i in items if i.get("pdf_url")]
    if not have_pdf:
        info("no titles have a companion PDF")
        return
    out = out or pdf_cache_dir()
    out.mkdir(parents=True, exist_ok=True)

    with open_session(ctx.obj["profile"]) as (auth, client):
        ok, fail = 0, []
        with step_progress("Fetching companion PDFs") as p:
            t = p.add_task("pdf", total=len(have_pdf))
            for item in have_pdf:
                got, reason = api.fetch_companion_pdf(auth, item, out / f"{item['asin']}.pdf")
                ok += int(got)
                if not got:
                    fail.append((item["title"], reason))
                p.advance(t)
    success(f"{ok}/{len(have_pdf)} PDFs in {out}")
    if fail:
        warn(f"{len(fail)} failed")


# --------------------------------------------------------------------- audit


@app.command()
def audit(
    path: Path = typer.Argument(...),
    json_out: Optional[Path] = typer.Option(None, "--json"),
):
    """Report chapter/cover/tag health for a folder of .m4b files. Read-only."""
    with console.status(f"Probing .m4b files in {path}...", spinner="dots12"):
        rows = audit_mod.audit_dir(path)
    if not rows:
        warn("no .m4b files found")
        raise typer.Exit(1)
    s = audit_mod.summarize(rows)

    panel(
        f"[bold]{s['n']}[/bold] files, [danger]{len(s['errors'])}[/danger] unreadable\n"
        f"chapters present: [bold]{s['with_chapters']}/{s['total_files']}[/bold] "
        f"([bold]{s['fully_titled_chapters']}[/bold] fully titled)\n"
        f"cover art: [bold]{s['covers']}/{s['total_files']}[/bold]",
        title=f"Audit — {path}",
    )
    table = make_table("Tag coverage", ["Tag", "Coverage"])
    for label, n in s["tag_coverage"].items():
        table.add_row(label, f"{n}/{s['total_files']}")
    console.print(table)

    if json_out:
        json_out.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
        success(f"wrote {json_out}")


# -------------------------------------------------------------------- doctor


_DOCTOR_STATUS = {
    "ok": ("success", "✓"),
    "warn": ("warn", "!"),
    "fail": ("danger", "✗"),
    "skip": ("dim", "·"),
}


def render_doctor(checks) -> str:
    """Print a doctor report and return the overall status. Shared by the CLI
    command and the interactive menu."""
    table = make_table("Preflight checks", ["", "Check", "Result"])
    for c in checks:
        style, icon = _DOCTOR_STATUS.get(c.status, ("dim", "·"))
        table.add_row(f"[{style}]{icon}[/{style}]", c.name, c.detail)
        if c.hint and c.status in ("warn", "fail"):
            table.add_row("", "", f"[dim]↳ {c.hint}[/dim]")
    console.print(table)

    overall = doctor_mod.worst_status(checks)
    if overall == "ok":
        success("all systems go — ready to download")
    elif overall == "warn":
        warn("usable, but some things could bite you — see the hints above")
    else:
        error("not ready — resolve the failing checks above before downloading")
    return overall


@app.command()
def doctor(
    ctx: typer.Context,
    online: bool = typer.Option(False, "--online", help="Also make a live Audible API call to confirm your auth token still works."),
):
    """Check that ffmpeg, your login, and the environment can actually download + decrypt."""
    checks = doctor_mod.run_checks(ctx.obj["profile"], online=online)
    overall = render_doctor(checks)
    if overall == "fail":
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
