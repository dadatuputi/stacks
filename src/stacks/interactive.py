"""The interactive menu you get from running `stacks` with no arguments."""

from __future__ import annotations

from pathlib import Path

import questionary
from questionary import Choice

from . import api, audit as audit_mod, auth as auth_mod, catalog, downloader, matcher, organizer, tagger
from .config import Settings, pdf_cache_dir
from .session import open_session
from .ui import THEME, banner, console, download_progress, error, info, make_table, panel, random_tagline, step_progress, success, warn

QMARK = "?"
_STYLE = questionary.Style(
    [
        ("qmark", "fg:#7dd3fc bold"),
        ("question", "bold"),
        ("answer", "fg:#c084fc bold"),
        ("pointer", "fg:#7dd3fc bold"),
        ("highlighted", "fg:#7dd3fc bold"),
        ("selected", "fg:#4ade80"),
    ]
)


def _select(message: str, choices, **kw):
    return questionary.select(message, choices=choices, style=_STYLE, qmark=QMARK, **kw).ask()


def _checkbox(message: str, choices, **kw):
    return questionary.checkbox(message, choices=choices, style=_STYLE, qmark=QMARK, **kw).ask()


def _text(message: str, **kw):
    return questionary.text(message, style=_STYLE, qmark=QMARK, **kw).ask()


def _confirm(message: str, default: bool = True):
    return questionary.confirm(message, default=default, style=_STYLE, qmark=QMARK).ask()


MENU_MAIN = [
    Choice("📥  Download audiobooks", value="download"),
    Choice("🏷️   Enrich existing .m4b files", value="enrich"),
    Choice("🗂️   Organize into Author/Title folders", value="organize"),
    Choice("📄  Fetch companion PDFs", value="pdfs"),
    Choice("🔍  Audit a folder's tag/chapter/cover health", value="audit"),
    Choice("🔄  Refresh library cache", value="sync"),
    Choice("🔑  Account", value="account"),
    Choice("🚪  Exit", value="exit"),
]


def run(profile: str = "default") -> None:
    console.clear()
    banner()

    status = auth_mod.status(profile)
    if not status["signed_in"]:
        info("You're not signed in yet.")
        how = _select(
            "How would you like to sign in?",
            [
                Choice("Log in to Audible now", "login"),
                Choice("Import an existing auth file (auth.txt / audible-cli)", "import"),
                Choice("« not now", "skip"),
            ],
        )
        try:
            if how == "login":
                auth_mod.login(profile)
            elif how == "import":
                path = _text("Path to the existing auth file:")
                if not path:
                    return
                encrypt = _confirm("Password-protect the copy stacks saves?", default=True)
                dest_password = questionary.password("Choose a password:").ask() if encrypt else None
                auth_mod.import_file(Path(path).expanduser(), profile, dest_password=dest_password)
            else:
                return
        except auth_mod.AuthError as e:
            error(str(e))
            return
    else:
        success(f"Signed in as {status.get('name', 'unknown')} ({status.get('locale', '?')})")
    console.print(f"  [dim]{random_tagline()}[/dim]\n")

    settings = Settings.load()

    while True:
        choice = _select("What would you like to do?", MENU_MAIN)
        if choice is None or choice == "exit":
            console.print("\n[accent]Happy listening. 🎧[/accent]\n")
            return
        try:
            _dispatch(choice, profile, settings)
        except KeyboardInterrupt:
            warn("cancelled")
        except Exception as e:  # noqa: BLE001
            error(f"unexpected error: {e}")
        console.print()


def _dispatch(choice: str, profile: str, settings: Settings) -> None:
    if choice == "sync":
        _menu_sync(profile)
    elif choice == "download":
        _menu_download(profile, settings)
    elif choice == "enrich":
        _menu_enrich(settings)
    elif choice == "organize":
        _menu_organize(settings)
    elif choice == "pdfs":
        _menu_pdfs(profile)
    elif choice == "audit":
        _menu_audit(settings)
    elif choice == "account":
        _menu_account(profile)


# ------------------------------------------------------------------ sync


def _menu_sync(profile: str) -> list[dict]:
    with open_session(profile) as (auth, client):
        with console.status("Fetching your Audible library...", spinner="dots12") as st:
            def cb(n):
                st.update(f"Fetching your Audible library... {n} so far")
            items = api.fetch_library(client, refresh=True, progress_cb=cb)
    success(f"Cached {len(items)} titles.")
    return items


def _ensure_library(profile: str) -> list[dict]:
    items = catalog.load_cached_items()
    if items:
        return items
    info("No cached library yet — fetching it now (one-time, ~1-2 min).")
    return _menu_sync(profile)


# ------------------------------------------------------------------ download


def pick_books(items: list[dict]) -> list[dict]:
    query = _text("Search your library (title/author/series — Enter for everything):") or ""
    matches = catalog.search_items(items, query)
    if not matches:
        warn("no matches")
        return []
    if len(matches) > 200:
        warn(f"{len(matches)} matches — showing the first 200; narrow your search for the rest.")
    choices = [Choice(catalog.label_for(i), value=i["asin"]) for i in matches[:200]]
    picked_asins = _checkbox("Select books to download (space to toggle, enter to confirm):", choices)
    if not picked_asins:
        return []
    by_asin = {i["asin"]: i for i in items}
    return [by_asin[a] for a in picked_asins]


def _menu_download(profile: str, settings: Settings) -> None:
    items = _ensure_library(profile)
    if not items:
        return
    picked = pick_books(items)
    if not picked:
        info("nothing selected")
        return

    dest = _text("Download into which folder?", default=settings.download_dir) or settings.download_dir
    quality = _select("Audio quality:", [Choice("High (best available)", "high"), Choice("Normal", "normal")], default="high") or settings.quality
    do_enrich = _confirm("Stamp full metadata + cover art after downloading?", default=settings.auto_enrich)
    do_organize = _confirm("Move into Author/Title folders after downloading?", default=settings.auto_organize)
    do_pdfs = _confirm("Fetch companion PDFs where available?", default=settings.fetch_pdfs)

    panel(
        f"[bold]{len(picked)}[/bold] book(s) → [bold]{dest}[/bold]\n"
        f"quality: {quality}   enrich: {do_enrich}   organize: {do_organize}   pdfs: {do_pdfs}",
        title="Ready to download",
        style=THEME["accent2"],
    )
    if not _confirm("Go?", default=True):
        return

    dest_path = Path(dest).expanduser()
    with open_session(profile) as (auth, client):
        run_downloads(auth, client, picked, dest_path, quality, settings.workers, do_enrich, do_organize, do_pdfs)


def run_downloads(auth, client, items, dest_path, quality, workers, do_enrich, do_organize, do_pdfs) -> None:
    progress = download_progress()
    task_ids = {}
    with progress:
        for item in items:
            task_ids[item["asin"]] = progress.add_task("download", label=item.get("title", item["asin"])[:48], total=None)

        def on_progress(asin, done, total):
            tid = task_ids.get(asin)
            if tid is None:
                return
            if total and progress.tasks[tid].total != total:
                progress.update(tid, total=total)
            progress.update(tid, completed=done)

        results = []

        def on_done(result):
            results.append(result)
            tid = task_ids.get(result.asin)
            if tid is not None:
                progress.update(tid, completed=progress.tasks[tid].total or 1)

        downloader.download_many(
            auth, client, items, dest_path, quality=quality, workers=workers,
            on_task_progress=on_progress, on_task_done=on_done,
        )

    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    success(f"{len(ok)}/{len(results)} downloaded to {dest_path}")
    if failed:
        warn(f"{len(failed)} failed:")
        for r in failed:
            console.print(f"    [danger]{r.title}[/danger] — {r.error}")

    by_asin = {i["asin"]: i for i in items}
    downloaded = [(r, by_asin[r.asin]) for r in ok if r.path]

    if do_enrich and downloaded:
        with step_progress("Stamping metadata") as p:
            t = p.add_task("tag", total=len(downloaded))
            opts = tagger.TagOptions()
            for r, item in downloaded:
                try:
                    tagger.stamp(r.path, item, opts)
                except Exception as e:  # noqa: BLE001
                    warn(f"tagging failed for {r.title}: {e}")
                p.advance(t)
        success("metadata stamped")

    if do_pdfs and downloaded:
        pdf_dir = pdf_cache_dir()
        with step_progress("Fetching companion PDFs") as p:
            t = p.add_task("pdf", total=len(downloaded))
            for r, item in downloaded:
                if item.get("pdf_url"):
                    api.fetch_companion_pdf(auth, item, pdf_dir / f"{item['asin']}.pdf")
                p.advance(t)
        success("companion PDFs fetched (see cache/pdfs)")

    if do_organize and downloaded:
        aliases = organizer.load_aliases(dest_path / "author_aliases.json")
        files = {r.path: r.asin for r, _ in downloaded}
        by_asin_map = {item["asin"]: item for _, item in downloaded}
        entries = organizer.plan(by_asin_map, files, aliases)
        log = organizer.apply_plan(entries, dest_path, pdf_cache_dir(), write_cover=True, copy=False)
        success(f"organized {len(entries)} book(s)")
        for line in log:
            console.print(f"    [dim]{line}[/dim]")


# ------------------------------------------------------------------ enrich


def _menu_enrich(settings: Settings) -> None:
    directory = _text("Folder of .m4b files to enrich:", default=settings.download_dir)
    if not directory:
        return
    d = Path(directory).expanduser()
    if not d.is_dir():
        error(f"{d} is not a directory")
        return

    items = catalog.load_cached_items()
    if not items:
        warn("no cached library — run 'Refresh library cache' first")
        return

    dry_run = _confirm("Dry run first (show what would change, write nothing)?", default=True)
    _run_enrich(d, items, dry_run)
    if dry_run and _confirm("Apply for real now?", default=False):
        _run_enrich(d, items, False)


def _run_enrich(directory: Path, items: list[dict], dry_run: bool) -> None:
    by_asin, by_title = matcher.build_index(items)
    files = sorted(directory.glob("*.m4b"))
    if not files:
        warn(f"no .m4b files in {directory}")
        return

    opts = tagger.TagOptions(dry_run=dry_run)
    matched, unmatched = 0, []
    table = make_table("Enrichment plan" if dry_run else "Enrichment results", ["File", "Match", "Changes"])
    with step_progress("Matching + tagging") as p:
        t = p.add_task("enrich", total=len(files))
        for f in files:
            result = matcher.match_file(f, by_asin, by_title)
            if result.item is None:
                unmatched.append((f, result.how))
                table.add_row(f.name[:50], "[danger]no match[/danger]", result.how[:40])
            else:
                matched += 1
                changed = tagger.stamp(f, result.item, opts)
                table.add_row(f.name[:50], f"[success]{result.how}[/success]", ", ".join(changed)[:60] or "-")
            p.advance(t)
    console.print(table)
    success(f"{matched}/{len(files)} matched")
    if unmatched:
        warn(f"{len(unmatched)} unmatched")


# ------------------------------------------------------------------ organize


def _menu_organize(settings: Settings) -> None:
    src = _text("Folder of .m4b files to organize:", default=settings.download_dir)
    if not src:
        return
    src_dir = Path(src).expanduser()
    out = _text("Destination library root:", default=str(src_dir / "Library"))
    if not out:
        return
    out_dir = Path(out).expanduser()

    items = catalog.load_cached_items()
    if not items:
        warn("no cached library — run 'Refresh library cache' first")
        return
    by_asin = {i["asin"]: i for i in items}

    alias_path = src_dir / "author_aliases.json"
    aliases = organizer.load_aliases(alias_path)
    if not aliases and _confirm("No author-alias file yet — suggest one now?", default=True):
        suggested = organizer.suggest_aliases(items)
        suggested_path = src_dir / "author_aliases.suggested.json"
        import json

        suggested_path.write_text(json.dumps(suggested, indent=1, ensure_ascii=False))
        info(f"wrote {len(suggested)} proposed merges to {suggested_path} — review, then rename to author_aliases.json")

    files, unmatched = {}, []
    for p in sorted(src_dir.glob("*.m4b")):
        asin = organizer.file_asin(p)
        if asin and asin in by_asin:
            files[p] = asin
        else:
            unmatched.append(p)
    if unmatched:
        warn(f"{len(unmatched)} files have no ASIN tag/filename match — run 'Enrich' first")
    if not files:
        error("nothing to organize")
        return

    entries = organizer.plan(by_asin, files, aliases)
    table = make_table("Organize plan", ["Destination", "Source file"])
    for e in sorted(entries, key=lambda e: str(e.dir)):
        table.add_row(f"{e.dir}/{e.m4b_name}", e.src.name[:50])
    console.print(table)

    if not _confirm(f"Move {len(entries)} book(s) into {out_dir}?", default=False):
        return
    copy = _confirm("Copy instead of move (uses ~2x disk, leaves originals in place)?", default=False)
    organizer.apply_plan(entries, out_dir, pdf_cache_dir(), write_cover=True, copy=copy)
    success(f"organized {len(entries)} book(s) into {out_dir}")


# ------------------------------------------------------------------ pdfs


def _menu_pdfs(profile: str) -> None:
    items = _ensure_library(profile)
    have_pdf = [i for i in items if i.get("pdf_url")]
    if not have_pdf:
        info("no titles in your library have a companion PDF")
        return
    dest_dir = Path(_text("Save PDFs to:", default=str(pdf_cache_dir())) or pdf_cache_dir()).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)

    with open_session(profile) as (auth, client):
        ok, fail = 0, []
        with step_progress("Fetching companion PDFs") as p:
            t = p.add_task("pdf", total=len(have_pdf))
            for item in have_pdf:
                success_, reason = api.fetch_companion_pdf(auth, item, dest_dir / f"{item['asin']}.pdf")
                if success_:
                    ok += 1
                else:
                    fail.append((item["title"], reason))
                p.advance(t)
    success(f"{ok}/{len(have_pdf)} PDFs in {dest_dir}")
    if fail:
        warn(f"{len(fail)} failed")
        for title, reason in fail[:10]:
            console.print(f"    [dim]{title[:50]}: {reason}[/dim]")


# ------------------------------------------------------------------ audit


def _menu_audit(settings: Settings) -> None:
    directory = _text("Folder to audit:", default=settings.download_dir)
    if not directory:
        return
    d = Path(directory).expanduser()
    if not d.is_dir():
        error(f"{d} is not a directory")
        return

    with console.status(f"Probing .m4b files in {d}...", spinner="dots12"):
        rows = audit_mod.audit_dir(d)
    if not rows:
        warn("no .m4b files found")
        return
    s = audit_mod.summarize(rows)

    panel(
        f"[bold]{s['n']}[/bold] files, [danger]{len(s['errors'])}[/danger] unreadable\n"
        f"chapters present: [bold]{s['with_chapters']}/{s['total_files']}[/bold] "
        f"([bold]{s['fully_titled_chapters']}[/bold] fully titled)\n"
        f"cover art: [bold]{s['covers']}/{s['total_files']}[/bold]",
        title=f"Audit — {d}",
        style=THEME["accent"],
    )

    table = make_table("Tag coverage", ["Tag", "Coverage"])
    for label, n in s["tag_coverage"].items():
        style = "success" if n == s["total_files"] else ("warn" if n else "danger")
        table.add_row(label, f"[{style}]{n}/{s['total_files']}[/{style}]")
    console.print(table)

    if s["no_chapters"]:
        warn(f"{len(s['no_chapters'])} file(s) with no chapters:")
        for f in s["no_chapters"][:10]:
            console.print(f"    [dim]{f}[/dim]")


# ------------------------------------------------------------------ account


def _menu_account(profile: str) -> None:
    choice = _select(
        "Account",
        [
            Choice("Show status", "status"),
            Choice("Import an existing auth file", "import"),
            Choice("Switch profile", "switch"),
            Choice("Sign out", "logout"),
            Choice("« back", "back"),
        ],
    )
    if choice == "status":
        s = auth_mod.status(profile)
        table = make_table("Account", ["Field", "Value"])
        for k, v in s.items():
            table.add_row(k, str(v))
        console.print(table)
    elif choice == "import":
        path = _text("Path to the existing auth file:")
        if not path:
            return
        encrypt = _confirm("Password-protect the copy stacks saves?", default=True)
        dest_password = questionary.password("Choose a password:").ask() if encrypt else None
        try:
            auth_mod.import_file(Path(path).expanduser(), profile, dest_password=dest_password)
            success(f"imported into profile '{profile}'")
        except auth_mod.AuthError as e:
            error(str(e))
    elif choice == "logout":
        if _confirm(f"Sign out profile '{profile}'? This deletes the local auth file.", default=False):
            auth_mod.logout(profile)
            success("signed out")
    elif choice == "switch":
        info("Run `stacks --profile <name>` to use a different saved login.")
