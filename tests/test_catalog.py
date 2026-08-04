from stacks.catalog import author_of, label_for, search_items, series_of


ITEMS = [
    {"asin": "B001", "title": "The Way of Kings", "authors": [{"name": "Brandon Sanderson"}], "series": [{"title": "The Stormlight Archive", "sequence": "1"}]},
    {"asin": "B002", "title": "Dracula", "authors": [{"name": "Bram Stoker"}]},
    {"asin": "B003", "title": "Norse Mythology", "authors": [{"name": "Neil Gaiman"}]},
]


def test_search_items_matches_title_and_author():
    assert [i["asin"] for i in search_items(ITEMS, "sanderson")] == ["B001"]
    assert [i["asin"] for i in search_items(ITEMS, "dracula")] == ["B002"]


def test_search_items_requires_all_terms():
    assert search_items(ITEMS, "brandon dracula") == []


def test_search_items_empty_query_returns_everything():
    assert len(search_items(ITEMS, "")) == 3


def test_author_and_series_helpers():
    assert author_of(ITEMS[0]) == "Brandon Sanderson"
    assert "Stormlight" in series_of(ITEMS[0])
    assert series_of(ITEMS[1]) == ""


def test_label_for_includes_author_and_title():
    label = label_for(ITEMS[0])
    assert "Brandon Sanderson" in label
    assert "The Way of Kings" in label
