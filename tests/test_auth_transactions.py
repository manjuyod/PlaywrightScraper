from __future__ import annotations

from http.cookies import SimpleCookie

import pytest
from flask import Flask, make_response

from ui.auth import transaction


def test_build_transaction_uses_32_random_bytes_and_ten_minute_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    values = iter(("state-token", "pkce-verifier"))

    def token_urlsafe(random_bytes: int) -> str:
        calls.append(random_bytes)
        return next(values)

    monkeypatch.setattr(transaction.secrets, "token_urlsafe", token_urlsafe)

    auth_tx = transaction.build_transaction("/", now=1_700_000_000)

    assert calls == [32, 32]
    assert auth_tx == transaction.AuthTransaction(
        state="state-token",
        code_verifier="pkce-verifier",
        return_path="/",
        expires_at=1_700_000_600,
    )


@pytest.mark.parametrize(
    "return_path",
    (
        "https://attacker.example/",
        "//attacker.example/",
        "/auth/callback",
        "/?next=https://attacker.example/",
        "dashboard",
    ),
)
def test_build_transaction_rejects_non_allowlisted_return_paths(
    return_path: str,
) -> None:
    with pytest.raises(ValueError, match="return path"):
        transaction.build_transaction(return_path, now=1_700_000_000)


def test_signed_transaction_rejects_tampering_and_expiry() -> None:
    auth_tx = transaction.AuthTransaction(
        state="state-token",
        code_verifier="pkce-verifier",
        return_path="/",
        expires_at=1_700_000_600,
    )
    signed = transaction.sign_transaction(auth_tx, "cookie-secret")

    assert transaction.load_transaction(
        signed, "cookie-secret", now=1_700_000_599
    ) == auth_tx
    with pytest.raises(ValueError, match="transaction"):
        transaction.load_transaction(
            f"{signed[:-1]}{'A' if signed[-1] != 'A' else 'B'}",
            "cookie-secret",
            now=1_700_000_599,
        )
    with pytest.raises(ValueError, match="transaction"):
        transaction.load_transaction(signed, "cookie-secret", now=1_700_000_600)


def test_transaction_cookie_is_host_only_secure_http_only_and_lax() -> None:
    app = Flask(__name__)
    with app.app_context():
        response = make_response("")
        transaction.set_transaction_cookie(response, "signed-value", max_age=600)

    header = response.headers.getlist("Set-Cookie")[0]
    cookie = SimpleCookie()
    cookie.load(header)
    morsel = cookie[transaction.TRANSACTION_COOKIE_NAME]

    assert morsel.value == "signed-value"
    assert morsel["path"] == "/"
    assert morsel["secure"] is True
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "Lax"
    assert morsel["max-age"] == "600"
    assert morsel["domain"] == ""
