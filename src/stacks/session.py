"""Small helper for acquiring an authenticated (Authenticator, Client) pair,
shared by both the interactive menu and the plain CLI commands."""

from __future__ import annotations

from contextlib import contextmanager

import audible

from . import auth as auth_mod


@contextmanager
def open_session(profile: str = "default"):
    authenticator = auth_mod.load(profile)
    with audible.Client(auth=authenticator) as client:
        yield authenticator, client
