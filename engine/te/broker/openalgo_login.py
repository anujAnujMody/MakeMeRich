"""Automated daily relogin to OpenAlgo's web app + Angel broker session.

Angel expires its broker session nightly (a real, documented Angel-platform
behaviour, not an OpenAlgo bug) — OpenAlgo itself ships no scripted/headless
login path for this (confirmed against its own docs: the only supported
route is the web UI). This module replays that exact browser flow over
HTTP, cookie-for-cookie:

    1. GET  /auth/csrf-token           -> a CSRF token tied to a fresh session
    2. POST /auth/login                -> OpenAlgo's own app account (username/password)
    3. POST /angel/callback            -> the Angel broker leg (client_id/pin/totp)

Deliberately stdlib-only for the TOTP computation (RFC 6238) rather than
pulling in `pyotp` for six lines of HMAC-SHA1 — see `compute_totp`.

Two-factor note: if OpenAlgo's OWN app account (step 2) has its own 2FA
enabled, this correctly fails with `LoginResult(ok=False, stage="app_totp_required",
...)` rather than silently hanging — that second TOTP secret is not
something this module has and is not the same secret as Angel's."""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
from dataclasses import dataclass

import httpx
import structlog

logger = structlog.get_logger(__name__)


def compute_totp(secret_base32: str, *, digits: int = 6, period: int = 30, at: float | None = None) -> str:
    """RFC 6238 TOTP over the given base32 secret — the same algorithm
    Google Authenticator/Angel's `enable-totp` flow uses. `at` defaults to
    `time.time()`; overridable only for tests (never for real use — a stale
    `at` produces a code the broker will reject)."""
    key = base64.b32decode(secret_base32.strip().upper() + "=" * (-len(secret_base32.strip()) % 8))
    counter = int((at if at is not None else time.time()) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code_int).zfill(digits)


@dataclass(frozen=True, slots=True)
class LoginResult:
    ok: bool
    stage: str  # "csrf" | "app_login" | "app_totp_required" | "broker_login" | "done"
    message: str


def login_openalgo(
    host: str,
    *,
    app_username: str,
    app_password: str,
    angel_client_id: str,
    angel_pin: str,
    angel_totp_secret: str,
    client: httpx.Client | None = None,
    timeout: float = 15.0,
) -> LoginResult:
    """Replays the OpenAlgo web login + Angel broker callback over one
    cookie-persistent HTTP session. Never raises on an expected failure
    path (bad credentials, CSRF rejection, 2FA on the app account) —
    those are reported as `LoginResult(ok=False, ...)` for the caller to
    log/alert on, matching every other broker-facing function in
    `te.broker`. Only genuine transport failures (host unreachable) raise,
    same as `OpenAlgoRestClient`."""
    owns_client = client is None
    # `Accept`/`X-Requested-With` force OpenAlgo's `is_ajax_request()` check
    # down the JSON branch on every response (success AND failure) — without
    # these, a failed broker login redirects to the HTML broker-selection
    # page instead of returning a checkable JSON body, which this module
    # would otherwise misread as a redirect-shaped "success".
    ajax_headers = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
    http = client or httpx.Client(base_url=host, timeout=timeout, follow_redirects=False, headers=ajax_headers)
    try:
        csrf_resp = http.get("/auth/csrf-token")
        if csrf_resp.status_code != 200:
            return LoginResult(False, "csrf", f"GET /auth/csrf-token -> HTTP {csrf_resp.status_code}")
        csrf_token = csrf_resp.json().get("csrf_token")
        if not csrf_token:
            return LoginResult(False, "csrf", "no csrf_token in response body")

        csrf_headers = {"X-CSRFToken": csrf_token}

        login_resp = http.post(
            "/auth/login", data={"username": app_username, "password": app_password}, headers=csrf_headers
        )
        login_body = _json_or_empty(login_resp)
        login_status = login_body.get("status")

        if login_status == "totp_required":
            return LoginResult(
                False,
                "app_totp_required",
                "OpenAlgo's own app account has 2FA enabled — this module only "
                "has Angel's TOTP secret, not the app account's. Disable app-level "
                "2FA for this account, or extend this module with a second secret.",
            )
        if login_status != "success":
            return LoginResult(
                False, "app_login", f"POST /auth/login -> HTTP {login_resp.status_code}: {login_body}"
            )

        if login_body.get("redirect") == "/dashboard":
            # `_try_resume_broker_session` (OpenAlgo's own `blueprints/
            # auth.py`) found an ALREADY-VALID Angel session for this app
            # account and revalidated it with a lightweight funds call — the
            # whole login (app + broker) is done right here. Calling
            # `/angel/callback` in this state hits its own
            # `if session.get("logged_in"): return redirect(...)` guard and
            # gets back a bare 302 with no JSON body, which this module would
            # otherwise misread as a failure.
            logger.info("openalgo relogin resumed an already-valid broker session")
            return LoginResult(True, "done", "resumed existing broker session")

        angel_totp = compute_totp(angel_totp_secret)
        broker_resp = http.post(
            "/angel/callback",
            data={"userid": angel_client_id, "pin": angel_pin, "totp": angel_totp},
            headers=csrf_headers,
        )
        broker_body = _json_or_empty(broker_resp)
        if broker_body.get("status") == "success":
            logger.info("openalgo relogin succeeded")
            return LoginResult(True, "done", "logged in")

        return LoginResult(
            False, "broker_login", f"POST /angel/callback -> HTTP {broker_resp.status_code}: {broker_body}"
        )
    finally:
        if owns_client:
            http.close()


def _json_or_empty(response: httpx.Response) -> dict[str, object]:
    if not response.headers.get("content-type", "").startswith("application/json"):
        return {}
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
