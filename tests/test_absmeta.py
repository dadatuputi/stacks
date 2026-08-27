import json
from pathlib import Path

from stacks import absmeta

MOSSFLOWER = {
    "asin": "B002V8MHS2",
    "title": "Mossflower",
    "subtitle": None,
    "authors": [{"asin": "B000AQ8SQM", "name": "Brian Jacques"}],
    "narrators": [{"name": "Brian Jacques"}, {"name": "Full Cast"}],
    "series": [{"asin": "B006K1R4B6", "sequence": "2", "title": "Redwall"}],
    "publisher_name": "Recorded Books",
    "release_date": "2004-10-29",
    "publisher_summary": "<p>One autumn evening, Bella of Brockhall &amp; friends...</p>",
    "isbn": "9781440781575",
    "language": "english",
    "is_adult_product": False,
    "format_type": "unabridged",
    "category_ladders": [
        {"ladder": [{"name": "Children's Audiobooks"}, {"name": "Action & Adventure"}, {"name": "Animals"}]},
        {"ladder": [{"name": "Children's Audiobooks"}, {"name": "Literature & Fiction"}]},
    ],
    # noise that must not reach metadata.json
    "runtime_length_min": 430,
    "sku": "BK_RECO_000123",
    "order_id": "D01-1234567-1234567",
    "customer_reviews": [{"rating": 5}],
    "plans": [{"plan_name": "Radio"}],
    "product_images": {"500": "https://example.invalid/cover.jpg"},
}


def test_reference_mapping():
    meta = absmeta.build_metadata(MOSSFLOWER)
    assert meta == {
        "title": "Mossflower",
        "subtitle": None,
        "authors": ["Brian Jacques"],
        "narrators": ["Brian Jacques", "Full Cast"],
        "series": ["Redwall #2"],
        "genres": ["Children's Audiobooks"],
        "tags": ["Action & Adventure", "Animals", "Literature & Fiction"],
        "publishedYear": "2004",
        "publishedDate": "2004-10-29",
        "publisher": "Recorded Books",
        "description": "One autumn evening, Bella of Brockhall & friends...",
        "isbn": "9781440781575",
        "asin": "B002V8MHS2",
        "language": "English",
        "explicit": False,
        "abridged": False,
    }


def test_no_key_outside_the_abs_schema():
    meta = absmeta.build_metadata(MOSSFLOWER, chapters=[{"start": 0, "end": 10.5, "title": "Opening Credits"}])
    assert set(meta) <= set(absmeta.ABS_KEYS)


def test_standalone_book_emits_empty_series():
    meta = absmeta.build_metadata({"asin": "B1", "title": "Standalone"})
    assert meta["series"] == []
    assert meta["authors"] == [] and meta["narrators"] == []
    assert meta["publishedYear"] is None and meta["explicit"] is None


def test_non_numeric_sequences_survive():
    for seq in ("1a", "2.5", "0.5"):
        item = {"series": [{"title": "Redwall", "sequence": seq}]}
        assert absmeta.build_metadata(item)["series"] == [f"Redwall #{seq}"]


def test_sequence_dropped_when_it_would_break_parse_series_string():
    # ABS parses with / #([^#\s]+)$/ — a sequence containing whitespace or a
    # second '#' gets swallowed into the series *name*, so drop it instead.
    for seq in ("1 a", "1#a", "vol 2"):
        item = {"series": [{"title": "Redwall", "sequence": seq}]}
        assert absmeta.build_metadata(item)["series"] == ["Redwall"]


def test_multi_author_and_narrator_are_flat_strings():
    item = {
        "authors": [{"name": "A One", "asin": "X"}, {"name": "B Two"}, {"name": "A One"}],
        "narrators": [{"name": "N One"}, {"name": "N Two"}],
    }
    meta = absmeta.build_metadata(item)
    assert meta["authors"] == ["A One", "B Two"]
    assert meta["narrators"] == ["N One", "N Two"]
    assert all(isinstance(v, str) for v in meta["authors"] + meta["narrators"])


def test_description_has_no_markup_or_entities():
    item = {"publisher_summary": "<p>First &amp; foremost.<br>Second &#39;line&#39;.</p><div>Third</div>"}
    desc = absmeta.build_metadata(item)["description"]
    for bad in ("<p>", "<br>", "</div>", "&amp;", "&#39;"):
        assert bad not in desc
    assert "First & foremost." in desc


def test_double_encoded_entities_are_fully_decoded():
    # Audible sometimes ships &amp;amp; (or a whole tag encoded as text); one
    # unescape pass would leave &amp; and <p> behind.
    item = {
        "title": "Cats &amp;amp; Dogs",
        "publisher_summary": "&lt;p&gt;Bella &amp;amp; friends&lt;/p&gt;",
    }
    meta = absmeta.build_metadata(item)
    assert meta["title"] == "Cats & Dogs"
    assert meta["description"] == "Bella & friends"


def test_description_falls_back_to_merchandising_summary():
    item = {"publisher_summary": "", "merchandising_summary": "<p>Short blurb</p>"}
    assert absmeta.build_metadata(item)["description"] == "Short blurb"


def test_published_year_is_a_four_char_string_from_a_datetime():
    item = {"publication_datetime": "2004-10-29T07:00:00Z"}
    meta = absmeta.build_metadata(item)
    assert meta["publishedYear"] == "2004" and isinstance(meta["publishedYear"], str)
    assert meta["publishedDate"] == "2004-10-29"


def test_abridged_is_mapped_explicitly_never_inferred_from_not_unabridged():
    # original_recording covers lecture series, talks and audio-first works —
    # ~7% of a real library, and none of them are abridgements.
    assert absmeta.build_metadata({"format_type": "abridged"})["abridged"] is True
    assert absmeta.build_metadata({"format_type": "Unabridged"})["abridged"] is False
    assert absmeta.build_metadata({"format_type": "original_recording"})["abridged"] is False
    # an unrecognized format is left null rather than guessed at
    assert absmeta.build_metadata({"format_type": "audio_drama"})["abridged"] is None
    assert absmeta.build_metadata({})["abridged"] is None
    assert absmeta.build_metadata({"is_adult_product": "true"})["explicit"] is True
    assert absmeta.build_metadata({"is_adult_product": "nope"})["explicit"] is None


def test_numeric_isbn_is_coerced_to_string():
    assert absmeta.build_metadata({"isbn": 9781440781575})["isbn"] == "9781440781575"


def test_chapters_from_audible_payload_flattens_and_converts_to_seconds():
    payload = {
        "chapter_info": {
            "chapters": [
                {"start_offset_ms": 0, "length_ms": 14392, "title": "Opening Credits"},
                {
                    "start_offset_ms": 14392,
                    "length_ms": 40586,
                    "title": "Epigraph",
                    "chapters": [{"start_offset_ms": 20000, "length_ms": 1000, "title": "Part One"}],
                },
            ]
        }
    }
    chapters = absmeta.chapters_from_audible(payload)
    assert chapters[0] == {"start": 0.0, "end": 14.392, "title": "Opening Credits"}
    assert chapters[1] == {"start": 14.392, "end": 54.978, "title": "Epigraph"}
    assert chapters[2]["title"] == "Part One"


def test_chapters_are_all_or_nothing():
    good = [{"start": 0, "end": 10, "title": "One"}]
    assert absmeta.clean_chapters(good) == [{"start": 0.0, "end": 10.0, "title": "One"}]
    for bad in (
        [{"start": 0, "end": 10, "title": "One"}, {"start": 10, "end": 20, "title": "  "}],
        [{"start": 0, "end": 10, "title": "One"}, {"start": 10, "end": 5, "title": "Two"}],
        [{"start": 0, "end": 10, "title": "One"}, {"start": "10", "end": 20, "title": "Two"}],
        [],
        None,
    ):
        assert absmeta.clean_chapters(bad) is None


def test_chapters_key_omitted_when_unusable():
    assert "chapters" not in absmeta.build_metadata(MOSSFLOWER)
    assert "chapters" not in absmeta.build_metadata(MOSSFLOWER, chapters=[{"start": 0, "end": 0, "title": "x"}])


def test_write_sidecars_names_the_raw_dump_audible_json(tmp_path: Path):
    raw, meta = absmeta.write_sidecars(tmp_path, MOSSFLOWER)
    assert raw.name == "audible.json" and meta.name == "metadata.json"
    assert json.loads(raw.read_text()) == MOSSFLOWER
    assert json.loads(meta.read_text())["authors"] == ["Brian Jacques"]
    assert "order_id" not in json.loads(meta.read_text())


def test_write_sidecars_overwrites_a_poisoned_legacy_metadata_json(tmp_path: Path):
    (tmp_path / "metadata.json").write_text(json.dumps(MOSSFLOWER))
    absmeta.write_sidecars(tmp_path, MOSSFLOWER)
    written = json.loads((tmp_path / "metadata.json").read_text())
    assert written["series"] == ["Redwall #2"]
    assert "customer_reviews" not in written
