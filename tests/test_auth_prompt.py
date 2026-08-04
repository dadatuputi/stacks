import pytest

from stacks import auth


class _Q:
    """Stand-in for questionary.text(...): .ask() returns queued answers."""

    def __init__(self, answers):
        self._answers = iter(answers)

    def ask(self):
        return next(self._answers)


def _patch_text(monkeypatch, answers):
    # One shared _Q so successive questionary.text(...).ask() calls advance
    # through `answers` — mirroring a real prompt that returns new input each
    # time, not the same first answer forever.
    q = _Q(answers)
    monkeypatch.setattr(auth.questionary, "text", lambda *a, **k: q)
    monkeypatch.setattr(auth, "warn", lambda *a, **k: None)  # silence re-prompt notice


def test_prompt_code_returns_first_nonempty(monkeypatch):
    _patch_text(monkeypatch, ["123456"])
    assert auth._prompt_code("code:") == "123456"


def test_prompt_code_reprompts_past_empty_and_whitespace(monkeypatch):
    _patch_text(monkeypatch, ["", "   ", "42"])
    assert auth._prompt_code("code:") == "42"


def test_prompt_code_strips_surrounding_whitespace(monkeypatch):
    _patch_text(monkeypatch, ["  9090  "])
    assert auth._prompt_code("code:") == "9090"


def test_prompt_code_cancel_raises_autherror(monkeypatch):
    _patch_text(monkeypatch, [None])  # questionary returns None on Ctrl-C / Esc
    with pytest.raises(auth.AuthError):
        auth._prompt_code("code:")
