from __future__ import annotations

import re
from pathlib import Path

from flask import Flask
from flask.sessions import SecureCookieSessionInterface

from ui.app import app, set_session_state
from ui import routes as _routes  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_uses_a_small_signed_cookie_with_key_fallback_support():
    assert isinstance(app.session_interface, SecureCookieSessionInterface)
    assert "SESSION_FILE_DIR" not in app.config
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

    with app.test_request_context("/"):
        set_session_state(
            session_type="crm",
            franchise_id=19,
            role=2,
            username=" Alice@Example.COM ",
        )
        from flask import session

        assert set(session) <= {
            "session_type",
            "franchise_id",
            "role",
            "user_fingerprint",
            "csrf_token",
            "active_job_ids",
            "_permanent",
        }
        assert session["user_fingerprint"] != "alice@example.com"
        assert len(session["user_fingerprint"]) == 64


def test_previous_session_key_verifies_old_cookies_during_rotation():
    old_app = Flask("old-session-key")
    old_app.secret_key = "old-session-secret-that-is-long-enough-2026"
    old_serializer = old_app.session_interface.get_signing_serializer(old_app)
    assert old_serializer is not None
    old_cookie = old_serializer.dumps({"session_type": "crm", "franchise_id": 19})

    rotated_app = Flask("rotated-session-key")
    rotated_app.secret_key = "new-session-secret-that-is-long-enough-2026"
    rotated_app.config["SECRET_KEY_FALLBACKS"] = [old_app.secret_key]
    rotated_serializer = rotated_app.session_interface.get_signing_serializer(rotated_app)
    assert rotated_serializer is not None

    assert rotated_serializer.loads(old_cookie)["franchise_id"] == 19


def test_dynamic_pages_emit_a_nonce_csp_and_security_headers():
    client = app.test_client()
    response = client.get("/login")
    assert response.status_code == 200

    csp = response.headers["Content-Security-Policy"]
    match = re.search(r"'nonce-([^']+)'", csp)
    assert match
    nonce = match.group(1)
    body = response.get_data(as_text=True)
    assert f'nonce="{nonce}"' in body
    assert "https://cdn." not in body
    assert "https://unpkg." not in body
    assert "unsafe-inline" not in csp
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"]


def test_frontend_assets_have_no_runtime_cdn_imports_and_are_lockfile_pinned():
    sources = [
        ROOT / "ui" / "templates" / "_react_assets.html",
        ROOT / "ui" / "static" / "react-dashboard.css",
        ROOT / "ui" / "static" / "styles.css",
    ]
    for source in sources:
        assert "http://" not in source.read_text(encoding="utf-8")
        assert "https://" not in source.read_text(encoding="utf-8")

    assets = sources[0].read_text(encoding="utf-8")
    assert "dist/dashboard.css" in assets
    assert "dist/dashboard.js" in assets
    assert (ROOT / "package-lock.json").is_file()
    assert (ROOT / "ui" / "static" / "dist" / "dashboard.css").is_file()
    assert (ROOT / "ui" / "static" / "dist" / "dashboard.js").is_file()
    bundle = (ROOT / "ui" / "static" / "dist" / "dashboard.js").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "Add Student",
        "edit_student",
        "add_student",
        "delete_students",
        "portal_password",
    ):
        assert forbidden not in bundle


def test_unbuilt_frontend_sources_are_not_publicly_served():
    client = app.test_client()
    for path in (
        "/static/react-dashboard.js",
        "/static/react-dashboard.css",
        "/static/styles.css",
    ):
        assert client.get(path).status_code == 404


def test_frontend_nginx_restores_only_cloudflare_ips_and_limits_login():
    nginx = (ROOT / "deploy" / "frontend" / "nginx" / "grade-frontend.conf").read_text(
        encoding="utf-8"
    )
    updater = (ROOT / "deploy" / "frontend" / "bin" / "update-cloudflare-ranges").read_text(
        encoding="utf-8"
    )
    assert "cloudflare-real-ip.conf" in nginx
    assert "POST $binary_remote_addr" in nginx
    assert "limit_req_zone $login_limit_key" in nginx
    assert "rate=30r/m" in nginx
    assert "location = /login" in nginx
    assert "limit_req zone=login_per_ip burst=10 nodelay" in nginx
    assert "limit_req_status 429" in nginx
    assert "CF-Connecting-IP" in updater
    assert "api.cloudflare.com/client/v4/ips" in updater
    assert "nginx -t" in updater
    assert "systemctl reload nginx" in updater
