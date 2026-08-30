from __future__ import annotations

from datetime import datetime, timezone
import secrets
from urllib.parse import urlencode

from flask import Blueprint, Response, make_response, redirect, render_template, request

from .client import ClientError, RustAuthClient
from .config import load_auth_config
from .session import (
    clear_session_cookie,
    create_session,
    set_session_cookie,
    sign_session,
)
from .transaction import (
    TRANSACTION_COOKIE_NAME,
    build_transaction,
    clear_transaction_cookie,
    format_code_challenge,
    load_transaction,
    set_transaction_cookie,
    sign_transaction,
)


bp = Blueprint("auth", __name__, template_folder="../templates")


@bp.get("/auth/start")
def start_auth() -> Response:
    config = load_auth_config()
    auth_tx = build_transaction("/", config.auth_transaction_ttl_seconds)
    query = urlencode(
        {
            "state": auth_tx.state,
            "code_challenge": format_code_challenge(auth_tx.code_verifier),
            "code_challenge_method": "S256",
        }
    )
    response = redirect(f"{config.crm_device_authorize_url}?{query}")
    set_transaction_cookie(
        response,
        sign_transaction(auth_tx, config.grade_checker_cookie_secret),
        max_age=config.auth_transaction_ttl_seconds,
    )
    return _no_store(response)


@bp.get("/auth/callback")
def callback() -> Response:
    config = load_auth_config()
    state_values = request.args.getlist("state")
    code_values = request.args.getlist("code")
    signed_transaction = request.cookies.get(TRANSACTION_COOKIE_NAME)
    if (
        len(state_values) != 1
        or len(code_values) != 1
        or not state_values[0]
        or not code_values[0]
        or not signed_transaction
    ):
        return _auth_error(400)

    try:
        auth_tx = load_transaction(signed_transaction, config.grade_checker_cookie_secret)
    except ValueError:
        return _auth_error(400)
    if not secrets.compare_digest(state_values[0], auth_tx.state):
        return _auth_error(400)

    try:
        client = RustAuthClient(config)
        claims = client.redeem_authorization_code(
            code_values[0], auth_tx.code_verifier
        )
        grant = client.introspect_grant(claims.grant_id, claims.sub)
    except ClientError:
        return _auth_error(503)

    now = _now()
    try:
        grade_session = create_session(claims, grant, now=now)
    except ValueError:
        return _auth_error(403)

    response = redirect(auth_tx.return_path)
    set_session_cookie(
        response,
        sign_session(grade_session, config.grade_checker_cookie_secret),
        grade_session,
        now=now,
    )
    clear_transaction_cookie(response)
    return _no_store(response)


@bp.route("/auth/logout", methods=["GET", "POST"])
def logout() -> Response:
    response = redirect("/")
    clear_session_cookie(response)
    clear_transaction_cookie(response)
    return _no_store(response)


def _auth_error(status: int) -> Response:
    response = make_response(
        render_template(
            "auth_error.html",
            message="Authentication could not be completed. No authorization details were stored.",
        ),
        status,
    )
    clear_transaction_cookie(response)
    return _no_store(response)


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _now() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())
