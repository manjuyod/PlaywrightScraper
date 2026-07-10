from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import TextIO

from api_transport import HttpsTransportProfile, TransportConfigError


class OperatorCliError(RuntimeError):
    pass


def _positive_student_id(value: str) -> int:
    try:
        student_id = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("student ID must be an integer") from exc
    if student_id <= 0:
        raise argparse.ArgumentTypeError("student ID must be positive")
    return student_id


def _validate_payload(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise OperatorCliError("Credential input is invalid")
    required: dict[str, str] = {}
    for field in ("portal_url", "username", "password"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise OperatorCliError("Credential input is incomplete")
        required[field] = value
    parsed_url = urllib.parse.urlsplit(required["portal_url"])
    if (
        parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.username is not None
        or parsed_url.password is not None
    ):
        raise OperatorCliError("Portal URL must be a valid HTTPS URL")
    return required


def _prompt_payload() -> dict[str, str]:
    return _validate_payload(
        {
            "portal_url": input("Alternate portal URL: "),
            "username": getpass.getpass("Alternate username: "),
            "password": getpass.getpass("Alternate password: "),
        }
    )


def _stdin_payload(stream: TextIO) -> dict[str, str]:
    raw = stream.read(16_385)
    if len(raw) > 16_384:
        raise OperatorCliError("Credential input is too large")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OperatorCliError("Credential input is invalid") from exc
    return _validate_payload(payload)


def _request(method: str, student_id: int, payload: dict[str, str] | None = None) -> None:
    api_key = os.getenv("OPERATOR_API_KEY", "")
    if not api_key or api_key.strip() != api_key:
        raise OperatorCliError("Operator API authentication is not configured")
    base_url = os.getenv("GRADE_API_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
    body = (
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if payload is not None
        else b""
    )
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if body:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base_url}/api/operator/students/{student_id}/alternate-credentials",
        data=body if body else None,
        headers=headers,
        method=method,
    )
    try:
        transport = HttpsTransportProfile.from_env(
            "OPERATOR_API", default_timeout_seconds=20, fallback_prefix="GRADE_API"
        )
        with transport.open(request):
            return
    except (TransportConfigError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise OperatorCliError("Operator API request failed") from exc


def put_alternate_credentials(student_id: int, payload: dict[str, str]) -> None:
    _request("PUT", student_id, payload)


def delete_alternate_credentials(student_id: int) -> None:
    _request("DELETE", student_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage encrypted alternate portal credentials through the private API"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    set_command = commands.add_parser("set")
    set_command.add_argument("student_id", type=_positive_student_id)
    set_command.add_argument(
        "--stdin",
        action="store_true",
        help="Read one credential JSON object from standard input",
    )
    delete_command = commands.add_parser("delete")
    delete_command.add_argument("student_id", type=_positive_student_id)
    return parser


def main(argv: list[str] | None = None, *, stdin: TextIO | None = None) -> int:
    args = _parser().parse_args(argv)
    stream = stdin or sys.stdin
    try:
        if args.command == "set":
            payload = _stdin_payload(stream) if args.stdin else _prompt_payload()
            put_alternate_credentials(args.student_id, payload)
            print("Alternate credentials updated.")
        else:
            delete_alternate_credentials(args.student_id)
            print("Alternate credentials cleared.")
    except OperatorCliError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
