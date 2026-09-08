import requests

import scraper.config.notifications as notification_config
from scraper.config.notifications import Severity, send_notification_to_slack


class DummyResponse:
    def __init__(self, status_code: int, text: str = "ok", headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if 400 <= self.status_code:
            err = requests.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err


def test_missing_webhook_skips(monkeypatch):
    monkeypatch.setattr(notification_config, "_ensure_dotenv_loaded", lambda: None)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    called = {"value": False}

    def fake_post(*_args, **_kwargs):
        called["value"] = True
        return DummyResponse(200)

    monkeypatch.setattr(notification_config.requests, "post", fake_post)

    status = send_notification_to_slack(Severity.Info, "hello")
    assert status == 0
    assert called["value"] is False


def test_success_posts_json(monkeypatch):
    monkeypatch.setattr(notification_config, "_ensure_dotenv_loaded", lambda: None)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.com/webhook")

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse(200)

    monkeypatch.setattr(notification_config.requests, "post", fake_post)

    status = send_notification_to_slack(Severity.Warn, "hi", max_attempts=1)
    assert status == 200
    assert captured["url"] == "https://example.com/webhook"
    assert captured["timeout"] == notification_config.DEFAULT_TIMEOUT_SECONDS
    assert captured["json"]["text"].startswith("[WARNING]")
    assert "hi" in captured["json"]["text"]


def test_rate_limit_retries(monkeypatch):
    monkeypatch.setattr(notification_config, "_ensure_dotenv_loaded", lambda: None)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.com/webhook")
    monkeypatch.setattr(notification_config.time, "sleep", lambda _s: None)

    calls = {"count": 0}

    def fake_post(_url, json=None, timeout=None):
        calls["count"] += 1
        if calls["count"] == 1:
            return DummyResponse(429, text="rate limited", headers={"Retry-After": "0"})
        return DummyResponse(200)

    monkeypatch.setattr(notification_config.requests, "post", fake_post)

    status = send_notification_to_slack(
        Severity.Info,
        "hi",
        max_attempts=2,
        retry_wait_seconds=0,
    )
    assert status == 200
    assert calls["count"] == 2
