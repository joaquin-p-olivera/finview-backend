"""Login with Google: checks the ID token Google Identity Services gives the frontend
and finds, links or creates the Finview user it belongs to."""

import re

from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models.user import User


settings = get_settings()

_request = google_requests.Request()


class GoogleLoginError(Exception):
    """The credential can't be used to sign in; the message is shown to the user."""


def verify_credential(credential: str) -> dict:
    """Claims of a valid Google ID token issued for our client ID (signature, expiry,
    issuer and audience are checked by google-auth)."""
    try:
        claims = id_token.verify_oauth2_token(credential, _request, settings.GOOGLE_CLIENT_ID)
    except TransportError as exc:
        # couldn't download Google's public keys
        raise GoogleLoginError("No se pudo contactar a Google, probá de nuevo") from exc
    except (ValueError, GoogleAuthError) as exc:
        raise GoogleLoginError("No se pudo validar la cuenta de Google") from exc
    if not claims.get("email") or not claims.get("email_verified"):
        raise GoogleLoginError("La cuenta de Google no tiene un email verificado")
    return claims


def _free_username(db: Session, email: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9._-]", "", email.split("@")[0])[:90] or "usuario"
    if len(base) < 3:
        base = f"{base}user"
    candidate, n = base, 1
    while db.query(User.id).filter(User.username == candidate).first():
        n += 1
        candidate = f"{base}{n}"
    return candidate


def user_for_google(db: Session, claims: dict) -> tuple[User, bool]:
    """The user for these claims and whether it was just created. An existing account
    with the same email gets linked (it keeps its password and data)."""
    google_id = claims["sub"]
    email = claims["email"]

    user = db.query(User).filter(User.google_id == google_id).first()
    if user:
        return user, False

    user = db.query(User).filter(func.lower(User.email) == email.lower()).first()
    if user:
        if user.google_id:
            raise GoogleLoginError("Esta cuenta ya está vinculada a otra cuenta de Google")
        user.google_id = google_id
        db.commit()
        db.refresh(user)
        return user, False

    user = User(email=email, username=_free_username(db, email), google_id=google_id)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, True
