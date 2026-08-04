"""The single-keypress menu: a shortcut key selects AND submits in one press."""

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from questionary import Choice

from stacks import interactive as I


def _run(keys: str, choices):
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        return I._key_select("pick", choices, inp=pipe, out=DummyOutput())


CHOICES = [
    Choice("Download", "download", shortcut_key="d"),
    Choice("Fetch PDFs", "pdfs", shortcut_key="p"),
    Choice("Exit", "exit", shortcut_key="q"),
]


def test_shortcut_key_selects_and_submits_in_one_press():
    assert _run("p", CHOICES) == "pdfs"
    assert _run("d", CHOICES) == "download"
    assert _run("q", CHOICES) == "exit"


def test_enter_confirms_the_highlighted_item():
    # cursor starts on the first item
    assert _run("\r", CHOICES) == "download"


def test_arrow_down_then_enter_moves_selection():
    assert _run("\x1b[B\r", CHOICES) == "pdfs"


def test_ctrl_c_cancels_to_none():
    assert _run("\x03", CHOICES) is None


def test_unbound_key_is_ignored_then_shortcut_wins():
    # 'z' has no binding and must not select anything; the following 'p' does
    assert _run("zp", CHOICES) == "pdfs"


def test_real_main_menu_shortcuts_are_wired():
    # every MENU_MAIN entry selects via its own key
    for ch in I.MENU_MAIN:
        assert _run(ch.shortcut_key, I.MENU_MAIN) == ch.value
