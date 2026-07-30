"""Tests for te.broker.openalgo_login — the automated daily OpenAlgo-app +
Angel-broker relogin, replayed over HTTP against a mocked transport (no live
network in this sandbox; see the accompanying live verification log for the
real end-to-end run against a running OpenAlgo instance)."""

from __future__ import annotations

import base64

import httpx

from te.broker.openalgo_login import LoginResult, compute_totp, login_openalgo

# RFC 6238 Appendix B's standard test vector: ASCII secret "12345678901234567890",
# SHA1, T=59s (counter=1 at a 30s period) -> the well-known 8-digit code
# "94287082". compute_totp truncates to 6 digits by construction (mod 10**6
# of the same untruncated integer), so the 6-digit code is exactly that
# 8-digit code's last 6 digits.
_RFC6238_SECRET_B32 = base64.b32encode(b"12345678901234567890").decode()


def test_compute_totp_matches_rfc6238_reference_vector() -> None:
    assert compute_totp(_RFC6238_SECRET_B32, digits=8, period=30, at=59) == "94287082"
    assert compute_totp(_RFC6238_SECRET_B32, digits=6, period=30, at=59) == "287082"


def test_compute_totp_changes_every_period() -> None:
    first = compute_totp(_RFC6238_SECRET_B32, at=0)
    same_period = compute_totp(_RFC6238_SECRET_B32, at=29)
    next_period = compute_totp(_RFC6238_SECRET_B32, at=30)
    assert first == same_period
    assert first != next_period


def _client_with_transport(handler) -> httpx.Client:  # noqa: ANN001
    return httpx.Client(base_url="http://openalgo:5000", transport=httpx.MockTransport(handler))


def test_login_openalgo_full_success_path() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/auth/csrf-token":
            return httpx.Response(200, json={"csrf_token": "tok-123"})
        if request.url.path == "/auth/login":
            assert request.headers["X-CSRFToken"] == "tok-123"
            form = dict(httpx.QueryParams(request.read().decode()))
            assert form == {"username": "app-user", "password": "app-pass"}
            return httpx.Response(200, json={"status": "success"})
        if request.url.path == "/angel/callback":
            form = dict(httpx.QueryParams(request.read().decode()))
            assert form["userid"] == "C123"
            assert form["pin"] == "1234"
            assert len(form["totp"]) == 6 and form["totp"].isdigit()
            return httpx.Response(
                200, json={"status": "success", "message": "Authentication successful", "redirect": "/dashboard"}
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    result = login_openalgo(
        "http://openalgo:5000",
        app_username="app-user",
        app_password="app-pass",
        angel_client_id="C123",
        angel_pin="1234",
        angel_totp_secret=_RFC6238_SECRET_B32,
        client=_client_with_transport(handler),
    )

    assert result == LoginResult(True, "done", "logged in")
    assert calls == ["/auth/csrf-token", "/auth/login", "/angel/callback"]


def test_login_openalgo_succeeds_on_resumed_broker_session_without_calling_angel_callback() -> None:
    """Regression, found live on 2026-07-30: a second relogin attempt within
    an already-valid Angel session window makes OpenAlgo's own `/auth/login`
    resume it internally (`redirect: "/dashboard"`) — `/angel/callback` must
    NOT be called in that case; it would hit that route's own
    already-logged-in guard and return a bare 302 with no JSON body, which
    an earlier version of this function misread as a failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/csrf-token":
            return httpx.Response(200, json={"csrf_token": "tok-123"})
        if request.url.path == "/auth/login":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "message": "Broker session resumed",
                    "redirect": "/dashboard",
                    "broker": "angel",
                },
            )
        raise AssertionError("must not call /angel/callback when the login response already resumed the session")

    result = login_openalgo(
        "http://openalgo:5000",
        app_username="app-user",
        app_password="app-pass",
        angel_client_id="C123",
        angel_pin="1234",
        angel_totp_secret=_RFC6238_SECRET_B32,
        client=_client_with_transport(handler),
    )

    assert result == LoginResult(True, "done", "resumed existing broker session")


def test_login_openalgo_reports_app_totp_required_instead_of_hanging() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/csrf-token":
            return httpx.Response(200, json={"csrf_token": "tok-123"})
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"status": "totp_required"})
        raise AssertionError("must not reach /angel/callback when app-level 2FA is pending")

    result = login_openalgo(
        "http://openalgo:5000",
        app_username="app-user",
        app_password="app-pass",
        angel_client_id="C123",
        angel_pin="1234",
        angel_totp_secret=_RFC6238_SECRET_B32,
        client=_client_with_transport(handler),
    )

    assert result.ok is False
    assert result.stage == "app_totp_required"


def test_login_openalgo_reports_bad_app_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/csrf-token":
            return httpx.Response(200, json={"csrf_token": "tok-123"})
        return httpx.Response(401, json={"status": "error", "message": "Invalid credentials"})

    result = login_openalgo(
        "http://openalgo:5000",
        app_username="app-user",
        app_password="wrong",
        angel_client_id="C123",
        angel_pin="1234",
        angel_totp_secret=_RFC6238_SECRET_B32,
        client=_client_with_transport(handler),
    )

    assert result.ok is False
    assert result.stage == "app_login"


def test_login_openalgo_reports_broker_leg_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/csrf-token":
            return httpx.Response(200, json={"csrf_token": "tok-123"})
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"status": "success"})
        return httpx.Response(200, json={"status": "error", "message": "Invalid TOTP"})

    result = login_openalgo(
        "http://openalgo:5000",
        app_username="app-user",
        app_password="app-pass",
        angel_client_id="C123",
        angel_pin="1234",
        angel_totp_secret=_RFC6238_SECRET_B32,
        client=_client_with_transport(handler),
    )

    assert result.ok is False
    assert result.stage == "broker_login"


def test_login_openalgo_reports_csrf_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    result = login_openalgo(
        "http://openalgo:5000",
        app_username="app-user",
        app_password="app-pass",
        angel_client_id="C123",
        angel_pin="1234",
        angel_totp_secret=_RFC6238_SECRET_B32,
        client=_client_with_transport(handler),
    )

    assert result.ok is False
    assert result.stage == "csrf"
