"""AAXC voucher decryption.

Audible's newer "AAXC" download format ships an encrypted voucher inside the
``licenserequest`` response containing the AES key/iv ffmpeg needs to
decrypt the file. The key material is derived from account/device identity
(never transmitted), so nothing here is a "crack" of Audible's DRM in the
sense of defeating a secret — it's the documented mechanism your own
authenticated device uses to play back audio it has a license for.

This re-implements the well-known derivation (device_type + serial +
customer_id + asin -> SHA256 -> AES-CBC key/iv) rather than importing the
``audible`` package's private ``aescipher`` module, so it keeps working even
if that internal module moves.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class VoucherDecryptError(RuntimeError):
    pass


def _key_iv(device_type: str, device_serial: str, customer_id: str, asin: str) -> tuple[bytes, bytes]:
    buf = f"{device_type}{device_serial}{customer_id}{asin}".encode("ascii")
    digest = hashlib.sha256(buf).digest()
    return digest[0:16], digest[16:32]


def decrypt_voucher(auth: Any, license_response: dict) -> dict:
    """Given the raw dict returned by POST content/{asin}/licenserequest,
    return the decrypted voucher as a dict with (at least) 'key' and 'iv'
    hex strings suitable for ffmpeg's -audible_key / -audible_iv."""
    try:
        content_license = license_response["content_license"]
        asin = content_license["asin"]
        encrypted_b64 = content_license["license_response"]
    except KeyError as e:
        raise VoucherDecryptError(f"license response missing {e} — Audible's API may have changed") from e

    device_info = getattr(auth, "device_info", None) or {}
    customer_info = getattr(auth, "customer_info", None) or {}
    device_type = device_info.get("device_type")
    device_serial = device_info.get("device_serial_number")
    customer_id = customer_info.get("user_id")
    if not all([device_type, device_serial, customer_id]):
        raise VoucherDecryptError(
            "authenticator is missing device_type/device_serial_number/user_id — "
            "re-run `stacks auth login`"
        )

    key, iv = _key_iv(device_type, device_serial, customer_id, asin)
    encrypted = base64.b64decode(encrypted_b64)

    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plaintext = decryptor.update(encrypted) + decryptor.finalize()

    # Strip PKCS7 padding if it's actually there.
    if plaintext and 1 <= plaintext[-1] <= 16 and plaintext[-plaintext[-1]:] == bytes([plaintext[-1]]) * plaintext[-1]:
        plaintext = plaintext[: -plaintext[-1]]

    try:
        return json.loads(plaintext)
    except (ValueError, UnicodeDecodeError):
        pass

    # Fallback: some responses pad with trailing junk that breaks strict JSON
    # parsing but the key/iv fields are still present as plain substrings.
    text = plaintext.decode("utf-8", errors="ignore")
    key_m = re.search(r'"key"\s*:\s*"([0-9a-fA-F]+)"', text)
    iv_m = re.search(r'"iv"\s*:\s*"([0-9a-fA-F]+)"', text)
    if key_m and iv_m:
        return {"key": key_m.group(1), "iv": iv_m.group(1)}

    raise VoucherDecryptError("could not parse decrypted voucher as JSON or extract key/iv")
