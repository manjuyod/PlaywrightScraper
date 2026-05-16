import os
import hmac
import time
from datetime import timedelta
from functools import wraps
import secrets

from dotenv import load_dotenv
from flask import Flask, abort, request, session
from flask_session import Session
from werkzeug.middleware.proxy_fix import ProxyFix

from db import Student, filter_group

load_dotenv()

DEFAULT_SESSION_SECRET = "dev-secret-key"

app = Flask(__name__, static_folder="static", template_folder="templates")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

SESSION_SECRET = os.getenv("SESSION_SECRET", DEFAULT_SESSION_SECRET)
app.secret_key = SESSION_SECRET

IS_DEPLOYMENT = os.getenv("REPLIT_DEPLOYMENT", "0") in {"1", "true", "True"}
IS_REPLIT = bool(os.getenv("REPL_ID") or os.getenv("REPLIT_DEV_DOMAIN"))
print(f"\nDeployment: {IS_DEPLOYMENT}")
if (
    SESSION_SECRET == DEFAULT_SESSION_SECRET
    or len(SESSION_SECRET.strip()) < 32
    or SESSION_SECRET.lower() in {"replace-me", "changeme", "secret"}
):
    raise ValueError(
        "SESSION_SECRET must be set to a strong, non-default value."
    )

# Session management
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = "ui/tmp"
app.config["SESSION_FILE_THRESHOLD"] = 100
app.config["SESSION_FILE_MODE"] = 0o600
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_COOKIE_SECURE"] = IS_DEPLOYMENT or IS_REPLIT
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

Session(app)

LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 5 * 60
_login_failures: dict[tuple[str, str], list[float]] = {}


def students_key(f_id: int) -> str:
    return f"students_{f_id}"

# session helpers
def get_students_from_session(franchise_id: int) -> list[Student] | None:
    s_key = students_key(franchise_id)
    students: list[Student] = session.get(s_key, None)
    return students


def store_students_in_session(franchise_id: int, students: list[Student]):
    s_key = students_key(franchise_id)
    session[s_key] = students


def add_student_to_session(franchise_id: int, student: Student):
    s_key = students_key(franchise_id)
    session[s_key].append(student)


def update_student_in_session(franchise_id: int, student: Student):
    s_key = students_key(franchise_id)
    student_removed = filter_group(session[s_key], "id", student.id, include=False)
    session[s_key] = student_removed.append(student)


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
    return {"csrf_token": ensure_csrf_token()}


def set_session_state(*, session_type: str, franchise_id: int | None, role: int | None = None) -> None:
    session.clear()
    session.permanent = True
    session["authorized"] = True
    session["session_type"] = session_type
    if franchise_id is not None:
        session["franchise_id"] = franchise_id
    if role is not None:
        session["role"] = role
    ensure_csrf_token()


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


def _job_franchise_id(job_id: str) -> int | None:
    if not job_id:
        return None
    return _coerce_session_int(job_id.split("_", 1)[0])


def _login_rate_key(remote_addr: str | None, username: str) -> tuple[str, str]:
    return (remote_addr or "unknown", username.strip().lower())


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
        if not session.get("authorized"):
            abort(403)

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
            request_franchise_id = _job_franchise_id(request_job_id or "")
            if request_franchise_id != franchise_id:
                abort(403)

        return await view(*args, **kwargs)

    return wrapped_view


@app.errorhandler(403)  # called on forbidden access to routes
async def forbidden(e):
    print("Access forbidden")
    session.clear()
    return {"error": "access forbidden"}, 403
