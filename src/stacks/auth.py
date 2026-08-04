"""Interactive login against Audible and local auth-file management.

Wraps ``audible.Authenticator`` with rich/questionary-driven callbacks so a
first-time login feels like a normal CLI wizard instead of a wall of raw
prompts. The resulting auth file can optionally be encrypted at rest with a
password you supply.
"""

from __future__ import annotations

import getpass
from pathlib import Path
from typing import Optional, Union

import audible
import questionary
from rich.console import Console

from . import config
from .ui import THEME, panel

console = Console()

LOCALES = ["us", "uk", "de", "fr", "ca", "au", "in", "it", "es", "jp", "br"]


class AuthError(RuntimeError):
    pass


def _captcha_callback(captcha_url: str) -> str:
    panel(
        "A CAPTCHA is required to continue.\n\n"
        f"Open this URL in a browser, then type what you see:\n[link]{captcha_url}[/link]",
        title="CAPTCHA",
        style=THEME["warn"],
    )
    return questionary.text("CAPTCHA answer:").ask() or ""


def _otp_callback() -> str:
    return questionary.text("Enter your one-time password (OTP/2FA code):").ask() or ""


def _cvf_callback() -> str:
    return questionary.text("Enter the verification code Audible sent you:").ask() or ""


def _approval_callback() -> None:
    panel(
        "Audible sent a login approval request to your registered device.\n"
        "Approve it there, then press Enter to continue.",
        title="Approval needed",
        style=THEME["warn"],
    )
    questionary.text("Press Enter once approved...").ask()


def login(profile: str = "default", locale: Optional[str] = None) -> audible.Authenticator:
    """Run the interactive login wizard and persist the resulting auth file."""
    console.print()
    panel(
        "stacks needs to sign in to your Audible account once. Credentials are "
        "sent directly to Audible over TLS via the official login flow (the same "
        "one audible-cli and the audible app use) and are never sent anywhere else.",
        title="🔑  Audible sign-in",
        style=THEME["accent"],
    )

    if locale is None:
        locale = questionary.select(
            "Which Audible marketplace is your account on?",
            choices=LOCALES,
            default="us",
        ).ask()
        if locale is None:
            raise AuthError("login cancelled")

    username = questionary.text("Audible email:").ask()
    if not username:
        raise AuthError("login cancelled")
    password = getpass.getpass("Audible password: ")
    if not password:
        raise AuthError("login cancelled")

    encrypt = questionary.confirm(
        "Encrypt the saved credentials with a password you choose?", default=True
    ).ask()
    file_password = None
    if encrypt:
        file_password = getpass.getpass("Choose a password to protect the local auth file: ")

    with console.status("Authenticating with Audible...", spinner="dots12"):
        auth = audible.Authenticator.from_login(
            username,
            password,
            locale=locale,
            with_username=False,
            captcha_callback=_captcha_callback,
            otp_callback=_otp_callback,
            cvf_callback=_cvf_callback,
            approval_callback=_approval_callback,
        )

    dest = config.auth_file(profile)
    auth.to_file(dest, password=file_password, encryption="json" if file_password else False)
    console.print(f"[success]✓ Signed in and saved to {dest}[/success]")
    return auth


def import_file(
    src: Union[Path, str],
    profile: str = "default",
    src_password: Optional[str] = None,
    dest_password: Optional[str] = None,
) -> audible.Authenticator:
    """Adopt an existing audible-format auth file — e.g. one made with
    audible-cli's `audible quickstart`, or a plain auth.txt produced by any
    script built on the `audible` package — instead of running the login
    wizard again. Re-saves it under this profile so `stacks` manages it
    going forward; the original file is left untouched."""
    src = Path(src)
    if not src.exists():
        raise AuthError(f"{src} does not exist")
    try:
        authenticator = audible.Authenticator.from_file(src, password=src_password)
    except Exception:
        if src_password is not None:
            raise
        src_password = getpass.getpass(f"Password for {src.name}: ")
        authenticator = audible.Authenticator.from_file(src, password=src_password)

    dest = config.auth_file(profile)
    authenticator.to_file(dest, password=dest_password, encryption="json" if dest_password else False)
    return authenticator


def load(profile: str = "default") -> audible.Authenticator:
    """Load a saved Authenticator, prompting for its password if encrypted."""
    dest = config.auth_file(profile)
    if not dest.exists():
        raise AuthError(
            f"no saved login for profile '{profile}' — run `stacks auth login` first"
        )
    try:
        return audible.Authenticator.from_file(dest)
    except Exception:
        pw = getpass.getpass(f"Password for auth file '{dest.name}': ")
        return audible.Authenticator.from_file(dest, password=pw)


def logout(profile: str = "default") -> bool:
    dest = config.auth_file(profile)
    if dest.exists():
        dest.unlink()
        return True
    return False


def status(profile: str = "default") -> dict:
    dest = config.auth_file(profile)
    if not dest.exists():
        return {"profile": profile, "signed_in": False, "path": str(dest)}
    try:
        auth = load(profile)
        customer = getattr(auth, "customer_info", {}) or {}
        return {
            "profile": profile,
            "signed_in": True,
            "path": str(dest),
            "name": customer.get("name") or customer.get("user_id", "unknown"),
            "locale": getattr(getattr(auth, "locale", None), "country_code", "?"),
        }
    except Exception as e:  # noqa: BLE001 — surface any load failure as status info
        return {"profile": profile, "signed_in": False, "path": str(dest), "error": str(e)}
