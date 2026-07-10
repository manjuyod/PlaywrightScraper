import os
import time
from enum import StrEnum
from pathlib import Path

import requests
from requests.exceptions import RequestException
from dotenv import load_dotenv

_DOTENV_LOADED = False


def _find_env_path() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    return None


def _ensure_dotenv_loaded() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    env_path = _find_env_path()
    if env_path is not None:
        load_dotenv(env_path)
    _DOTENV_LOADED = True

class Severity(StrEnum):
    Info = '[INFO]'
    Warn = '[WARNING]'
    Crit = '[CRIT]'

TOO_MANY_REQUESTS_ERR_CODE = 429
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_WAIT_SECONDS = 5
DEFAULT_TIMEOUT_SECONDS = 10


def _parse_retry_after(value: str | None, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        return max(0, int(value))
    except ValueError:
        return fallback


def send_notification_to_slack(
    severity: Severity,
    message: str,
    *,
    webhook_url: str | None = None,
    max_attempts: int = DEFAULT_RETRY_ATTEMPTS,
    retry_wait_seconds: int = DEFAULT_RETRY_WAIT_SECONDS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
):
    _ensure_dotenv_loaded()
    webhook_url = webhook_url or os.getenv('SLACK_WEBHOOK_URL')
    if not webhook_url:
        # raise ValueError('Slack Webhook Environment Variable DNE')
        print("[notif] SLACK_WEBHOOK_URL not set; skipping Slack notification", flush=True)
        return 0

    payload = {
        "text": severity + '\n' + message
    }

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(webhook_url, json=payload, timeout=timeout_seconds)
        except RequestException:
            print(
                f"[notif] Slack request failed (attempt {attempt}/{max_attempts})",
                flush=True,
            )
            if attempt < max_attempts:
                time.sleep(retry_wait_seconds)
                continue
            return None

        if response.status_code == TOO_MANY_REQUESTS_ERR_CODE:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"), retry_wait_seconds)
            print(f"[notif] Slack rate limited; retrying in {retry_after}s (attempt {attempt}/{max_attempts})", flush=True)
            if attempt < max_attempts:
                time.sleep(retry_after)
                continue
            return None

        try:
            response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)
        except RequestException:
            status = response.status_code
            print(
                f"[notif] Slack HTTP {status} (attempt {attempt}/{max_attempts})",
                flush=True,
            )
            if 400 <= status < 500:
                return None
            if attempt < max_attempts:
                time.sleep(retry_wait_seconds)
                continue
            return None

        print("Slack notification sent", flush=True)
        return response.status_code

if __name__ == "__main__":
    send_notification_to_slack(Severity.Info, 'This is a test message')
