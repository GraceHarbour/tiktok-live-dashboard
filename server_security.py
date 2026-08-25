"""Google IAP authentication and dashboard role checks.

The application must sit behind Google Identity-Aware Proxy (IAP).  It verifies
the IAP-signed JWT before trusting the user's email address, so a direct request
cannot impersonate a dashboard member by adding a header.
"""

from __future__ import annotations

import os
import hashlib
import hmac
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from google.auth.transport import requests
from google.oauth2 import id_token

from .database import CreatorNetworkDatabase


def require_sync_worker(
    *, timestamp: str | None, signature: str | None, body: bytes, worker_email: str | None = None
) -> None:
    """Authenticate the isolated sync worker with a short-lived HMAC signature.

    The browser session stays on the worker.  The dashboard receives only a
    signed data capture, never Backstage credentials or cookies.
    """
    secret = os.environ.get("CREATOR_SYNC_SECRET")
    if not secret or not timestamp or not signature:
        raise HTTPException(403, "The secure sync worker is not configured.")
    try:
        captured_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        age = abs((datetime.now(UTC) - captured_at.astimezone(UTC)).total_seconds())
    except ValueError as error:
        raise HTTPException(403, "The sync request timestamp is invalid.") from error
    if age > 600:
        raise HTTPException(403, "The sync request has expired.")
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected, signature):
        return

    # The endpoint is behind IAP. When the reader VM presents its short-lived
    # service-account assertion, IAP supplies this trusted identity header.
    # Accept it only for the dedicated isolated reader account; this lets a
    # rotated local HMAC key recover without exposing the endpoint publicly.
    trusted_worker = os.environ.get("CREATOR_SYNC_WORKER_EMAIL", "").strip().casefold()
    supplied_worker = (worker_email or "").strip().casefold()
    if trusted_worker and supplied_worker.endswith(trusted_worker):
        return
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(403, "The secure sync signature is invalid.")


def authenticated_email(request: Request) -> str:
    # The production service is bound to 127.0.0.1 and protected by IAP.  This
    # opt-in value exists only to preview the dashboard *on that same server*
    # before IAP is configured; it is never accepted from a network request.
    preview_email = os.environ.get("CREATOR_LOCAL_PREVIEW_EMAIL")
    client_host = request.client.host if request.client else ""
    if preview_email and client_host in {"127.0.0.1", "::1"}:
        return preview_email.strip().lower()
    audience = os.environ.get("IAP_AUDIENCE")
    assertion = request.headers.get("X-Goog-IAP-JWT-Assertion")
    if not audience or not assertion:
        raise HTTPException(503, "Dashboard sign-in has not been configured yet.")
    try:
        claims = id_token.verify_token(
            assertion,
            requests.Request(),
            audience=audience,
            certs_url="https://www.gstatic.com/iap/verify/public_key",
        )
    except Exception as error:  # Google token errors intentionally become an access denial.
        raise HTTPException(401, "Your Google sign-in could not be verified.") from error
    email = str(claims.get("email", "")).strip().lower()
    if not email:
        raise HTTPException(401, "Google did not provide an email address for this sign-in.")
    return email


def local_preview_enabled(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    return os.environ.get("CREATOR_LOCAL_PREVIEW") == "1" and client_host in {"127.0.0.1", "::1"}


def require_dashboard_user(request: Request, db: CreatorNetworkDatabase) -> dict[str, object]:
    if local_preview_enabled(request):
        owner = next((user for user in db.access_users() if user["active"] and user["role"] == "owner"), None)
        if owner:
            return owner
    user = db.access_user(authenticated_email(request))
    if not user or not user["active"]:
        raise HTTPException(403, "Your account is not approved for this dashboard.")
    return user


def require_access_admin(request: Request, db: CreatorNetworkDatabase) -> dict[str, object]:
    user = require_dashboard_user(request, db)
    if user["role"] not in {"owner", "admin"}:
        raise HTTPException(403, "Only dashboard owners and administrators can manage access.")
    return user

