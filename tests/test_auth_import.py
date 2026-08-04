from pathlib import Path

import pytest

from stacks import auth as auth_mod


def test_import_file_missing_path_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKS_HOME", str(tmp_path / "stackshome"))
    with pytest.raises(auth_mod.AuthError):
        auth_mod.import_file(tmp_path / "does-not-exist.txt", profile="test")
