"""Encrypts small secrets stored in the database (bank PDF passwords) with
Fernet. The key is derived from `SECRET_KEY` with HKDF, so it is independent of
the key that signs the login tokens. Rotating `SECRET_KEY` makes stored secrets
unreadable; they are then treated as missing and the user enters them again."""

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import get_settings


def _fernet() -> Fernet:
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"finview-secret-box",
    ).derive(get_settings().SECRET_KEY.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
