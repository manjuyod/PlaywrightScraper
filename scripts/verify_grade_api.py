from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any


def _json_body(payload: Any | None) -> bytes:
    if payload is None:
        return b""
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _request(
    method: str,
    url: str,
    *,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    tls_context: ssl.SSLContext | None = None,
) -> tuple[int, Any]:
    req = urllib.request.Request(
        url,
        data=body if body else None,
        method=method,
        headers=headers or {},
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=tls_context) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    if not raw:
        return status, None
    try:
        return status, json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return status, raw.decode("utf-8", errors="replace")


def _dashboard_headers(
    *,
    secret: str,
    method: str,
    path: str,
    body: bytes,
    franchise_id: int,
    role: int,
    user: str,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or str(uuid.uuid4())
    body_hash = hashlib.sha256(body).hexdigest()
    message = "\n".join(
        (
            timestamp,
            method.upper(),
            path,
            str(franchise_id),
            str(role),
            user,
            nonce,
            body_hash,
        )
    )
    signature = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Api-Timestamp": timestamp,
        "X-Api-Franchise-Id": str(franchise_id),
        "X-Api-Role": str(role),
        "X-Api-User": user,
        "X-Api-Nonce": nonce,
        "X-Api-Signature": signature,
    }


def _tls_context(
    *,
    ca_file: str | None,
    client_cert_file: str | None,
    client_key_file: str | None,
) -> ssl.SSLContext | None:
    if not any((ca_file, client_cert_file, client_key_file)):
        return None
    if bool(client_cert_file) != bool(client_key_file):
        raise ValueError("TLS client certificate and key must be configured together")

    context = ssl.create_default_context(cafile=ca_file or None)
    if client_cert_file and client_key_file:
        context.load_cert_chain(client_cert_file, client_key_file)
    return context


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the grade scraper API boundary.")
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("GRADE_API_BASE_URL", "http://127.0.0.1:3000"),
        help="Base URL for the Rust API.",
    )
    parser.add_argument(
        "--franchise-id",
        type=int,
        default=int(os.getenv("VERIFY_FRANCHISE_ID", "0") or "0"),
        help="Franchise id to use for the dashboard merge check.",
    )
    parser.add_argument(
        "--role",
        type=int,
        default=int(os.getenv("VERIFY_ROLE", "2") or "2"),
        help="Dashboard role header to sign with.",
    )
    parser.add_argument(
        "--ca-file",
        default=os.getenv("GRADE_API_CA_FILE"),
        help="Private CA bundle used to verify the API Nginx certificate.",
    )
    parser.add_argument(
        "--client-cert-file",
        default=os.getenv("GRADE_API_CLIENT_CERT_FILE"),
        help="mTLS client certificate for this verification identity.",
    )
    parser.add_argument(
        "--client-key-file",
        default=os.getenv("GRADE_API_CLIENT_KEY_FILE"),
        help="mTLS private key for this verification identity.",
    )
    args = parser.parse_args()

    secret = os.getenv("DASHBOARD_HMAC_SIGNING_SECRET", "")
    readiness_token = os.getenv("READINESS_API_TOKEN", "")
    _require(
        secret,
        "DASHBOARD_HMAC_SIGNING_SECRET is required for signed dashboard checks",
    )
    _require(readiness_token, "READINESS_API_TOKEN is required for readiness checks")
    _require(args.franchise_id > 0, "--franchise-id or VERIFY_FRANCHISE_ID is required")

    base_url = args.api_base_url.rstrip("/")
    tls_context = _tls_context(
        ca_file=args.ca_file,
        client_cert_file=args.client_cert_file,
        client_key_file=args.client_key_file,
    )

    status, payload = _request("GET", f"{base_url}/livez", tls_context=tls_context)
    _require(status == 200, f"/livez returned HTTP {status}")
    _require(payload == {"status": "ok"}, "/livez returned an unexpected body")
    print("liveness: ok")

    status, payload = _request(
        "GET",
        f"{base_url}/readyz",
        headers={"Authorization": f"Bearer {readiness_token}"},
        tls_context=tls_context,
    )
    _require(status == 200, f"/readyz returned HTTP {status}")
    _require(payload == {"status": "ready"}, "/readyz reported unavailable dependencies")
    print("readiness: ok")

    path = "/api/students"
    headers = _dashboard_headers(
        secret=secret,
        method="GET",
        path=path,
        body=b"",
        franchise_id=args.franchise_id,
        role=args.role,
        user="verify-grade-api",
    )
    status, payload = _request(
        "GET", f"{base_url}{path}", headers=headers, tls_context=tls_context
    )
    _require(status == 200, f"/api/students returned HTTP {status}")
    _require(isinstance(payload, dict), "/api/students did not return an object")
    _require(isinstance(payload.get("students"), list), "/api/students missing students list")
    print(f"dashboard merge: ok ({len(payload['students'])} students)")

    status, _payload = _request(
        "POST",
        f"{base_url}/api/worker/jobs/claim",
        tls_context=tls_context,
    )
    _require(status == 401, f"worker claim without auth returned HTTP {status}, expected 401")
    print("worker auth missing-key rejection: ok")

    status, _payload = _request(
        "POST",
        f"{base_url}/api/worker/jobs/claim",
        headers={
            "Authorization": "Bearer definitely-wrong",
        },
        tls_context=tls_context,
    )
    _require(status == 401, f"worker claim wrong auth returned HTTP {status}, expected 401")
    print("worker auth wrong-key rejection: ok")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
