from stacks.matcher import MatchResult, build_index, match, match_prefix, match_tokens


def _item(asin, title, subtitle=None, runtime=None):
    d = {"asin": asin, "title": title, "runtime_length_min": runtime}
    if subtitle:
        d["subtitle"] = subtitle
    return d


LIBRARY = [
    _item("B001", "Dracula", runtime=600),
    _item("B002", "The Hobbit", subtitle="An Unexpected Journey", runtime=650),
    _item("B003", "The Life-Changing Magic of Tidying Up", runtime=240),
    _item("B004", "The Life-Changing Magic of Tidying Up", runtime=260),  # duplicate title, diff edition
]


def test_build_index_by_asin_and_title():
    by_asin, by_title = build_index(LIBRARY)
    assert by_asin["B001"]["title"] == "Dracula"
    assert len(by_title["dracula"]) == 1


def test_match_by_asin_tag():
    by_asin, by_title = build_index(LIBRARY)
    tags = {"----:com.apple.iTunes:ASIN": [b"B001"]}
    result = match(__import__("pathlib").Path("whatever.m4b"), tags, None, by_asin, by_title)
    assert result.item["asin"] == "B001"
    assert result.how == "asin-tag"


def test_match_by_unambiguous_title():
    by_asin, by_title = build_index(LIBRARY)
    tags = {"\xa9nam": ["Dracula"]}
    result = match(__import__("pathlib").Path("dracula.m4b"), tags, None, by_asin, by_title)
    assert result.item["asin"] == "B001"
    assert result.how == "title"


def test_match_ambiguous_title_resolved_by_duration():
    by_asin, by_title = build_index(LIBRARY)
    tags = {"\xa9nam": ["The Life-Changing Magic of Tidying Up"]}
    result = match(__import__("pathlib").Path("f.m4b"), tags, 241, by_asin, by_title)
    assert result.item["asin"] == "B003"
    assert result.how == "title+duration"


def test_match_ambiguous_title_without_duration_fails_safe():
    by_asin, by_title = build_index(LIBRARY)
    tags = {"\xa9nam": ["The Life-Changing Magic of Tidying Up"]}
    result = match(__import__("pathlib").Path("f.m4b"), tags, None, by_asin, by_title)
    assert result.item is None
    assert "ambiguous" in result.how


def test_match_prefix_requires_duration_agreement():
    by_asin, by_title = build_index(LIBRARY)
    # "The Hobbit" is a prefix of "The Hobbit: An Unexpected Journey"
    hit = match_prefix("The Hobbit", 651, by_title)
    assert hit.item["asin"] == "B002"
    miss = match_prefix("The Hobbit", 10, by_title)  # wildly wrong runtime
    assert miss.item is None
