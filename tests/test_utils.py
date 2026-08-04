from stacks.utils import human_duration, human_size, norm, same_person, sanitize, strip_audible


def test_norm_strips_accents_punctuation_and_unabridged():
    assert norm("J.R.R. Tolkien's Book: A Tale (Unabridged)") == "j r r tolkien s book a tale"
    assert norm("Café Society") == "cafe society"
    assert norm("") == ""
    assert norm(None) == ""


def test_sanitize_replaces_colons_and_slashes():
    assert sanitize("Author: Subtitle / Weird*Chars") == "Author - Subtitle & Weird-Chars"
    assert sanitize(None) == "Unknown"
    assert sanitize("A" * 200, maxlen=10).__len__() <= 10


def test_sanitize_strips_audible_originals_boilerplate():
    assert sanitize("Twain's Feast: An Audible Original") == "Twain's Feast"


def test_strip_audible_can_return_empty():
    assert strip_audible("Audible Original") == ""
    assert strip_audible(None) is None


def test_same_person_matches_initials_and_typos():
    assert same_person("J.R.R. Tolkien", "J. R. R. Tolkien")
    assert same_person("L.M. Montgomery", "Lucy Maud Montgomery")
    assert same_person("Gabor Mate", "Gabor Maté")
    assert not same_person("Robert Bly", "Robert Moore")
    assert not same_person("Jane Austen", "John Austen")


def test_human_size_and_duration():
    assert human_size(500) == "500B"
    assert "KB" in human_size(2048)
    assert human_duration(3661) == "1h 1m"
    assert human_duration(90) == "1m 30s"
    assert human_duration(45) == "45s"
