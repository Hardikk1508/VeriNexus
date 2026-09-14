"""Firebase ID-token verification for protected routes.

Degrades to a no-op (every request treated as anonymous) if
FIREBASE_SERVICE_ACCOUNT_JSON is not set, so local development without a
Firebase project keeps working — same pattern as audit.py and MONGODB_URI.
"""

import base64
import json
import logging

from fastapi import Header, HTTPException

from app.config import settings

log = logging.getLogger("verinexus.auth")

_app = None
_checked = False


def _firebase_app():
    global _app, _checked
    if _checked:
        return _app
    _checked = True
    if not settings.FIREBASE_SERVICE_ACCOUNT_JSON:
        log.info("FIREBASE_SERVICE_ACCOUNT_JSON not set — auth is disabled")
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials

        raw = settings.FIREBASE_SERVICE_ACCOUNT_JSON
        # Accept either raw JSON or base64-encoded JSON — base64 is easier to
        # paste as a single-line value into a hosting dashboard.
        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            info = json.loads(base64.b64decode(raw))
        _app = firebase_admin.initialize_app(credentials.Certificate(info))
        log.info("Firebase auth initialised")
    except Exception as exc:  # noqa: BLE001
        log.warning("Firebase auth unavailable, disabling: %s", exc)
        _app = None
    return _app


def enabled() -> bool:
    return _firebase_app() is not None


def verify_token(token: str) -> dict:
    """Verify a Firebase ID token string. Returns its decoded claims."""
    app = _firebase_app()
    if app is None:
        return {"uid": "anonymous"}
    if not token:
        raise HTTPException(401, "Missing auth token.")
    try:
        from firebase_admin import auth as fb_auth

        return fb_auth.verify_id_token(token, app=app)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(401, f"Invalid or expired token: {exc}") from exc


async def require_user(authorization: str = Header(default="")) -> dict:
    """FastAPI dependency for routes that take the token via Authorization header."""
    token = authorization.removeprefix("Bearer ").strip()
    return verify_token(token)
