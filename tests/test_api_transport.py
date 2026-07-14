from __future__ import annotations

import urllib.request

import pytest

import api_transport


class _FakeContext:
    def __init__(self):
        self.loaded: tuple[str, str] | None = None

    def load_cert_chain(self, certfile: str, keyfile: str) -> None:
        self.loaded = (certfile, keyfile)


def test_https_transport_builds_a_mutual_tls_context(monkeypatch):
    context = _FakeContext()
    captured: dict[str, object] = {}

    def fake_create_default_context(*, cafile: str):
        captured["cafile"] = cafile
        return context

    def fake_urlopen(request, timeout, *, context):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["context"] = context
        return "response"

    monkeypatch.setenv("WORKER_API_CA_FILE", "ca.pem")
    monkeypatch.setenv("WORKER_API_CLIENT_CERT_FILE", "worker.crt")
    monkeypatch.setenv("WORKER_API_CLIENT_KEY_FILE", "worker.key")
    monkeypatch.setenv("WORKER_API_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setattr(api_transport.ssl, "create_default_context", fake_create_default_context)
    monkeypatch.setattr(api_transport.urllib.request, "urlopen", fake_urlopen)

    profile = api_transport.HttpsTransportProfile.from_env(
        "WORKER_API", default_timeout_seconds=30
    )
    response = profile.open(urllib.request.Request("https://api.internal/livez"))

    assert response == "response"
    assert captured == {
        "cafile": "ca.pem",
        "url": "https://api.internal/livez",
        "timeout": 12.5,
        "context": context,
    }
    assert context.loaded == ("worker.crt", "worker.key")


def test_production_https_uses_system_trust_without_client_cert(monkeypatch):
    captured = {}

    def fake_urlopen(request, *, timeout):
        captured.update(url=request.full_url, timeout=timeout)
        return "response"

    monkeypatch.setenv("DEPLOYMENT_ENV", "production")
    monkeypatch.setattr(api_transport.urllib.request, "urlopen", fake_urlopen)
    profile = api_transport.HttpsTransportProfile.from_env(
        "WORKER_API", default_timeout_seconds=30
    )
    response = profile.open(
        urllib.request.Request("https://grades-api-dev.tutoringclub.com/livez")
    )
    assert response == "response"
    assert captured == {
        "url": "https://grades-api-dev.tutoringclub.com/livez",
        "timeout": 30,
    }
    assert profile.ssl_context() is None


def test_custom_ca_is_optional(monkeypatch):
    context = _FakeContext()
    captured = {}

    def fake_create_default_context(*, cafile=None):
        captured["cafile"] = cafile
        return context

    monkeypatch.setenv("GRADE_API_CA_FILE", "private-ca.pem")
    monkeypatch.setattr(api_transport.ssl, "create_default_context", fake_create_default_context)

    profile = api_transport.HttpsTransportProfile.from_env(
        "GRADE_API", default_timeout_seconds=20
    )

    assert profile.ssl_context() is context
    assert captured == {"cafile": "private-ca.pem"}
    assert context.loaded is None


@pytest.mark.parametrize(
    "values",
    [
        {"GRADE_API_CLIENT_CERT_FILE": "client.crt"},
        {"GRADE_API_CLIENT_KEY_FILE": "client.key"},
    ],
)
def test_optional_client_cert_requires_complete_pair(monkeypatch, values):
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(api_transport.TransportConfigError):
        api_transport.HttpsTransportProfile.from_env(
            "GRADE_API", default_timeout_seconds=20
        )


def test_optional_client_cert_pair_works_without_custom_ca(monkeypatch):
    context = _FakeContext()

    monkeypatch.setenv("GRADE_API_CLIENT_CERT_FILE", "client.crt")
    monkeypatch.setenv("GRADE_API_CLIENT_KEY_FILE", "client.key")
    monkeypatch.setattr(
        api_transport.ssl,
        "create_default_context",
        lambda *, cafile=None: context,
    )

    profile = api_transport.HttpsTransportProfile.from_env(
        "GRADE_API", default_timeout_seconds=20
    )

    assert profile.ssl_context() is context
    assert context.loaded == ("client.crt", "client.key")


def test_production_rejects_http(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_ENV", "production")
    profile = api_transport.HttpsTransportProfile.from_env(
        "GRADE_API", default_timeout_seconds=20
    )

    with pytest.raises(api_transport.TransportConfigError):
        profile.open(urllib.request.Request("http://127.0.0.1:3000/livez"))


@pytest.mark.parametrize("value", ["0", "0.9", "121", "not-a-number"])
def test_https_transport_timeout_is_bounded(monkeypatch, value):
    monkeypatch.setenv("GRADE_API_TIMEOUT_SECONDS", value)
    with pytest.raises(api_transport.TransportConfigError):
        api_transport.HttpsTransportProfile.from_env(
            "GRADE_API", default_timeout_seconds=20
        )
