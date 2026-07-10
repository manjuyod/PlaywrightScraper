from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from scripts import verify_grade_api


def test_python_matches_the_shared_rust_hmac_vector() -> None:
    vector_path = (
        Path(__file__).resolve().parents[1]
        / "api"
        / "testdata"
        / "dashboard_hmac_vectors.json"
    )
    vector = json.loads(vector_path.read_text(encoding="utf-8"))[0]
    headers = verify_grade_api._dashboard_headers(
        secret=vector["secret"],
        method=vector["method"],
        path=vector["path"],
        body=vector["body"].encode("utf-8"),
        franchise_id=int(vector["franchise_id"]),
        role=int(vector["role"]),
        user=vector["user"],
        timestamp=vector["timestamp"],
        nonce=vector["nonce"],
    )
    assert headers["X-Api-Signature"] == vector["signature"]


def test_dashboard_headers_match_the_canonical_hmac_contract() -> None:
    timestamp = "1700000000"
    nonce = "00000000-0000-0000-0000-000000000042"
    body = b'{"kind":"grade"}'

    headers = verify_grade_api._dashboard_headers(
        secret="test-secret",
        method="post",
        path="/api/jobs/manual-pull?source=verify",
        body=body,
        franchise_id=11,
        role=2,
        user="user-fingerprint",
        timestamp=timestamp,
        nonce=nonce,
    )

    body_hash = hashlib.sha256(body).hexdigest()
    message = "\n".join(
        (
            timestamp,
            "POST",
            "/api/jobs/manual-pull?source=verify",
            "11",
            "2",
            "user-fingerprint",
            nonce,
            body_hash,
        )
    )
    expected = hmac.new(b"test-secret", message.encode(), hashlib.sha256).hexdigest()

    assert headers["X-Api-Nonce"] == nonce
    assert headers["X-Api-User"] == "user-fingerprint"
    assert headers["X-Api-Signature"] == expected


def test_tls_context_requires_a_complete_client_certificate_pair() -> None:
    with pytest.raises(ValueError, match="certificate and key"):
        verify_grade_api._tls_context(
            ca_file="ca.pem",
            client_cert_file="client.pem",
            client_key_file=None,
        )
