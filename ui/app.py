import os
import hmac
import time
from datetime import timedelta
from functools import wraps
import secrets
import hashlib
import unicodedata

from dotenv import load_dotenv
from flask import Flask, abort, g, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

DEFAULT_SESSION_SECRET = "dev-secret-key"

app = Flask(__name__, static_folder="static", template_folder="templates")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

SESSION_SECRET = os.getenv("SESSION_SECRET", DEFAULT_SESSION_SECRET)
app.secret_key = SESSION_SECRET

DEPLOYMENT_ENV = os.getenv("DEPLOYMENT_ENV", "development").strip().lower()
if DEPLOYMENT_ENV not in {"development", "test", "production"}:
    raise ValueError("DEPLOYMENT_ENV must be production, development, or test")
IS_PRODUCTION = DEPLOYMENT_ENV == "production"
if (
    SESSION_SECRET == DEFAULT_SESSION_SECRET
    or len(SESSION_SECRET.strip()) < 32
    or SESSION_SECRET.lower() in {"replace-me", "changeme", "secret"}
):
    raise ValueError(
        "SESSION_SECRET must be set to a strong, non-default value."
    )

previous_session_secret = os.getenv("SESSION_SECRET_PREVIOUS", "").strip()
if previous_session_secret:
    if len(previous_session_secret) < 32 or previous_session_secret == SESSION_SECRET:
        raise ValueError(
            "SESSION_SECRET_PREVIOUS must be a distinct strong previous key."
        )
    app.config["SECRET_KEY_FALLBACKS"] = [previous_session_secret]

app.config["SESSION_COOKIE_NAME"] = (
    "__Host-grade_session" if IS_PRODUCTION else "grade_session"
)
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_PATH"] = "/"
app.config["SESSION_REFRESH_EACH_REQUEST"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 5 * 60
_login_failures: dict[tuple[str, str], list[float]] = {}


def _coerce_session_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _session_franchise_id() -> int | None:
    return _coerce_session_int(session.get("franchise_id"))


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def ensure_csrf_token() -> str:
    token = session.get("csrf_token")
    if not isinstance(token, str):
        token = generate_csrf_token()
        session["csrf_token"] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {
        "csrf_token": ensure_csrf_token(),
        "csp_nonce": g.csp_nonce,
    }


def _username_fingerprint(username: str) -> str:
    normalized = unicodedata.normalize("NFKC", username).strip().casefold()
    return hmac.new(
        SESSION_SECRET.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def set_session_state(
    *,
    session_type: str,
    franchise_id: int | None,
    username: str,
    role: int | None = None,
) -> None:
    session.clear()
    session.permanent = True
    session["session_type"] = session_type
    session["user_fingerprint"] = _username_fingerprint(username)
    session["active_job_ids"] = {}
    if franchise_id is not None:
        session["franchise_id"] = franchise_id
    if role is not None:
        session["role"] = role
    ensure_csrf_token()


@app.before_request
def create_csp_nonce() -> None:
    g.csp_nonce = secrets.token_urlsafe(24)


@app.before_request
def block_unbuilt_frontend_sources() -> None:
    if request.path in {
        "/static/react-dashboard.js",
        "/static/react-dashboard.css",
        "/static/styles.css",
    }:
        abort(404)


@app.after_request
def apply_security_headers(response):
    nonce = g.get("csp_nonce", "")
    response.headers["Content-Security-Policy"] = "; ".join(
        (
            "default-src 'none'",
            "base-uri 'none'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            f"script-src 'self' 'nonce-{nonce}'",
            f"style-src 'self' 'nonce-{nonce}'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "manifest-src 'self'",
        )
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    )
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


def validate_csrf_token() -> None:
    if request.method != "POST":
        return

    expected = session.get("csrf_token")
    submitted = request.form.get("csrf_token")
    if not isinstance(expected, str) or not isinstance(submitted, str):
        abort(403)
    if not hmac.compare_digest(expected, submitted):
        abort(403)


def csrf_protect(view):
    @wraps(view)
    async def wrapped_view(*args, **kwargs):
        validate_csrf_token()
        return await view(*args, **kwargs)

    return wrapped_view


def _session_active_job_ids() -> set[str]:
    value = session.get("active_job_ids")
    if not isinstance(value, dict):
        return set()
    return {
        job_id
        for job_id in value.values()
        if isinstance(job_id, str) and job_id
    }


def _login_rate_key(remote_addr: str | None, username: str) -> tuple[str, str]:
    normalized_username = unicodedata.normalize("NFKC", username).strip().casefold()
    return (remote_addr or "unknown", normalized_username)


def _pruned_login_failures(key: tuple[str, str], now: float) -> list[float]:
    cutoff = now - LOGIN_ATTEMPT_WINDOW_SECONDS
    failures = [ts for ts in _login_failures.get(key, []) if ts >= cutoff]
    _login_failures[key] = failures
    return failures


def is_login_rate_limited(remote_addr: str | None, username: str) -> bool:
    now = time.monotonic()
    key = _login_rate_key(remote_addr, username)
    return len(_pruned_login_failures(key, now)) >= LOGIN_ATTEMPT_LIMIT


def record_login_failure(remote_addr: str | None, username: str) -> None:
    now = time.monotonic()
    key = _login_rate_key(remote_addr, username)
    failures = _pruned_login_failures(key, now)
    failures.append(now)
    _login_failures[key] = failures


def clear_login_failures(remote_addr: str | None, username: str) -> None:
    _login_failures.pop(_login_rate_key(remote_addr, username), None)


# route helpers
def login_required(view):
    @wraps(view)
    async def wrapped_view(*args, **kwargs):
        session_type = session.get("session_type")
        if session_type == "health_test":
            if request.endpoint in {"health", "logout"}:
                return await view(*args, **kwargs)

            if request.path in {"/health", "/logout"}:
                return await view(*args, **kwargs)

            abort(403)

        if session_type != "crm":
            abort(403)

        franchise_id = _session_franchise_id()
        if not franchise_id or franchise_id == 1:
            abort(403)

        if request.endpoint == "health":
            abort(403)
        if request.endpoint in {"franchise_view", "student_view"}:
            request_franchise_id = _coerce_session_int(kwargs.get("franchise_id"))
            if request_franchise_id != franchise_id:
                abort(403)
        elif request.endpoint == "status":
            request_job_id = request.view_args.get("job_id") if request.view_args else None
            if request_job_id not in _session_active_job_ids():
                abort(403)

        return await view(*args, **kwargs)

    return wrapped_view


@app.errorhandler(403)  # called on forbidden access to routes
async def forbidden(e):
    session.clear()
    return {"error": "access forbidden"}, 403
