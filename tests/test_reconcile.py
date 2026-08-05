from pathlib import Path

from stacks.reconcile import ReconcileReport, reconcile, scan_local


def _item(asin, title, author="Bram Stoker"):
    return {"asin": asin, "title": title, "authors": [{"name": author}]}


def _touch_m4b(directory: Path, name: str) -> Path:
    p = directory / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")  # empty stub — file_asin resolves via the _ASIN filename suffix
    return p


# --------------------------------------------------------------- reconcile()


def test_reconcile_flags_missing_and_present():
    items = [_item("B001", "Dracula"), _item("B002", "Frankenstein"), _item("B003", "Carmilla")]
    found = {"B001": [Path("Dracula_B001.m4b")]}
    report = reconcile(items, found)
    assert {i["asin"] for i in report.present} == {"B001"}
    assert {i["asin"] for i in report.missing} == {"B002", "B003"}
    assert report.total_library == 3


def test_reconcile_reports_unknown_asin_on_disk():
    items = [_item("B001", "Dracula")]
    found = {"B001": [Path("a.m4b")], "ZZZZZZZZZZ": [Path("b.m4b")]}
    report = reconcile(items, found)
    assert report.unknown == ["ZZZZZZZZZZ"]
    assert not report.missing


def test_reconcile_detects_duplicates():
    items = [_item("B001", "Dracula")]
    found = {"B001": [Path("one.m4b"), Path("copy/one.m4b")]}
    report = reconcile(items, found)
    assert report.duplicates == {"B001": [Path("one.m4b"), Path("copy/one.m4b")]}


def test_reconcile_passes_through_unresolved():
    report = reconcile([_item("B001", "Dracula")], {}, [Path("mystery.m4b")])
    assert report.unresolved == [Path("mystery.m4b")]
    assert len(report.missing) == 1


def test_reconcile_missing_sorted_by_author_then_title():
    items = [
        _item("B001", "Zeta", author="Zadie Smith"),
        _item("B002", "Alpha", author="Alan Moore"),
        _item("B003", "Beta", author="Alan Moore"),
    ]
    report = reconcile(items, {})
    assert [i["asin"] for i in report.missing] == ["B002", "B003", "B001"]


# ----------------------------------------------------------------- scan_local()


def test_scan_local_recurses_and_resolves_by_filename(tmp_path):
    # ASIN_IN_NAME only matches a 10-char ASIN suffix, so filenames use real-shaped ASINs.
    _touch_m4b(tmp_path, "Dracula_B06Y4G67WB.m4b")
    _touch_m4b(tmp_path, "Author/Title/Frankenstein_B0788XYZ12.m4b")  # organizer's nested layout
    found, unresolved = scan_local(tmp_path)
    assert set(found) == {"B06Y4G67WB", "B0788XYZ12"}
    assert unresolved == []


def test_scan_local_collects_files_with_no_asin(tmp_path):
    _touch_m4b(tmp_path, "Dracula_B06Y4G67WB.m4b")
    mystery = _touch_m4b(tmp_path, "ripped-somewhere.m4b")
    found, unresolved = scan_local(tmp_path)
    assert set(found) == {"B06Y4G67WB"}
    assert unresolved == [mystery]


def test_scan_then_reconcile_end_to_end(tmp_path):
    items = [_item("B06Y4G67WB", "Dracula"), _item("B0788XYZ12", "Frankenstein")]
    _touch_m4b(tmp_path, "Dracula_B06Y4G67WB.m4b")
    found, unresolved = scan_local(tmp_path)
    report = reconcile(items, found, unresolved)
    assert [i["asin"] for i in report.missing] == ["B0788XYZ12"]
    assert [i["asin"] for i in report.present] == ["B06Y4G67WB"]


def test_empty_report_defaults():
    report = ReconcileReport()
    assert report.total_library == 0
    assert report.missing == []
