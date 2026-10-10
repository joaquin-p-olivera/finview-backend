"""Encrypts small secrets stored in the database (bank PDF passwords) with
Fernet. The key is `SECRET_BOX_KEY` (a Fernet key) or, if unset, derived from
`SECRET_KEY`. Changing the key makes stored secrets unreadable; they are then
treated as missing and the user enters them again."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    if settings.SECRET_BOX_KEY:
        return Fernet(settings.SECRET_BOX_KEY.encode())
    digest = hashlib.sha256(f"secret-box:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
