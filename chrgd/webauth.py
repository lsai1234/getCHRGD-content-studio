"""Single-user auth for the web dashboard.

Password verification uses stdlib pbkdf2 (no extra dependency). Configure
either `CHRGD_WEB_PASSWORD_HASH` (preferred) or `CHRGD_WEB_PASSWORD`. Generate
a hash with:  `python -m chrgd.webauth <password>`.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys

from .config import Settings

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 200_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${dk.hex()}"


def _verify_hash(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def auth_configured(settings: Settings) -> bool:
    return bool(settings.web_password_hash or settings.web_password)


def verify_credentials(settings: Settings, username: str, password: str) -> bool:
    """Constant-time-ish credential check for the single configured user."""
    if not hmac.compare_digest(username or "", settings.web_username):
        return False
    if settings.web_password_hash:
        return _verify_hash(password, settings.web_password_hash)
    if settings.web_password:
        return hmac.compare_digest(password or "", settings.web_password)
    return False  # no password configured — refuse all logins


if __name__ == "__main__":  # `python -m chrgd.webauth <password>`
    if len(sys.argv) != 2:
        print("usage: python -m chrgd.webauth <password>", file=sys.stderr)
        raise SystemExit(2)
    print(hash_password(sys.argv[1]))
