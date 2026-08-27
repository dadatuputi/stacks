from pathlib import Path

from stacks.organizer import plan, suggest_aliases


def _item(asin, title, author="Bram Stoker", narrators=None, year=None):
    return {
        "asin": asin,
        "title": title,
        "authors": [{"name": author}],
        "narrators": [{"name": n} for n in (narrators or [])],
        "publication_datetime": f"{year}-01-01" if year else None,
    }


def test_plan_no_collision_uses_base_name():
    by_asin = {"B001": _item("B001", "Dracula")}
    files = {Path("dracula.m4b"): "B001"}
    entries = plan(by_asin, files, {})
    assert entries[0].book == "Bram Stoker - Dracula"
    assert entries[0].why == ""


def test_plan_collision_disambiguates_by_narrator():
    by_asin = {
        "B001": _item("B001", "Dracula", narrators=["Alan Cumming"]),
        "B002": _item("B002", "Dracula", narrators=["Tim Curry"]),
    }
    files = {Path("d1.m4b"): "B001", Path("d2.m4b"): "B002"}
    entries = plan(by_asin, files, {})
    books = {e.book for e in entries}
    assert len(books) == 2
    assert all("(" in b for b in books)
    assert all(e.why == "narrator" for e in entries)


def test_plan_collision_falls_back_to_asin_when_narrators_tie():
    by_asin = {
        "B001": _item("B001", "Same Book", narrators=["Same Narrator"], year="2015"),
        "B002": _item("B002", "Same Book", narrators=["Same Narrator"], year="2015"),
    }
    files = {Path("a.m4b"): "B001", Path("b.m4b"): "B002"}
    entries = plan(by_asin, files, {})
    books = {e.book for e in entries}
    assert len(books) == 2
    assert all("[" in b for b in books)  # ASIN-qualified


def test_plan_applies_author_aliases():
    by_asin = {"B001": _item("B001", "Book", author="J.R.R. Tolkien")}
    files = {Path("f.m4b"): "B001"}
    entries = plan(by_asin, files, {"J.R.R. Tolkien": "J. R. R. Tolkien"})
    assert entries[0].author == "J. R. R. Tolkien"


def test_suggest_aliases_merges_initials_and_full_names():
    items = [_item("B001", "T1", author="L.M. Montgomery"), _item("B002", "T2", author="Lucy Maud Montgomery")]
    out = suggest_aliases(items)
    assert out.get("L.M. Montgomery") == "Lucy Maud Montgomery"


def test_suggest_aliases_never_merges_different_people():
    items = [_item("B001", "T1", author="Robert Bly"), _item("B002", "T2", author="Robert Moore")]
    out = suggest_aliases(items)
    assert out == {}


def test_apply_plan_writes_abs_metadata_and_raw_dump(tmp_path):
    import json

    from stacks.organizer import apply_plan

    src = tmp_path / "in" / "dracula.m4b"
    src.parent.mkdir()
    src.write_bytes(b"not really an m4b")
    item = _item("B001", "Dracula", narrators=["Alan Cumming"], year=1897)
    item["series"] = [{"title": "Classics", "sequence": "3"}]
    entries = plan({"B001": item}, {src: "B001"}, {})

    out = tmp_path / "out"
    apply_plan(entries, out, pdf_dir=None, write_cover=False, copy=False)
    book_dir = out / entries[0].dir

    assert json.loads((book_dir / "audible.json").read_text()) == item
    meta = json.loads((book_dir / "metadata.json").read_text())
    assert meta["authors"] == ["Bram Stoker"]
    assert meta["series"] == ["Classics #3"]
    assert "publication_datetime" not in meta


def test_apply_plan_refreshes_metadata_for_an_already_organized_book(tmp_path):
    import json

    from stacks.organizer import apply_plan

    src = tmp_path / "in" / "dracula.m4b"
    src.parent.mkdir()
    src.write_bytes(b"not really an m4b")
    item = _item("B001", "Dracula")
    entries = plan({"B001": item}, {src: "B001"}, {})

    out = tmp_path / "out"
    book_dir = out / entries[0].dir
    book_dir.mkdir(parents=True)
    (book_dir / entries[0].m4b_name).write_bytes(b"already here")
    # a legacy raw dump under the name Audiobookshelf reads as authoritative
    (book_dir / "metadata.json").write_text(json.dumps(item))

    log = apply_plan(entries, out, pdf_dir=None, write_cover=False, copy=False)

    assert "SKIP" in log[0]
    assert json.loads((book_dir / "metadata.json").read_text())["authors"] == ["Bram Stoker"]
    assert (book_dir / "audible.json").exists()
    assert src.exists()  # the skip must not consume the source file
