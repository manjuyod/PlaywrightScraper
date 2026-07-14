from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from scraper.diagnostics import (
    sensitive_browser_artifacts_enabled,
    sensitive_tracing_context,
)
from scraper.safe_logging import suppress_portal_output
from scripts.check_python_boundaries import boundary_violations


def test_production_never_enables_sensitive_browser_artifacts(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("WORKER_ALLOW_SENSITIVE_BROWSER_ARTIFACTS", "1")
    assert sensitive_browser_artifacts_enabled() is False


@pytest.mark.parametrize("value", [None, "", "0", "false", "no"])
def test_nonproduction_artifacts_require_explicit_opt_in(monkeypatch, value):
    monkeypatch.setenv("DEPLOYMENT_ENV", "development")
    if value is None:
        monkeypatch.delenv("WORKER_ALLOW_SENSITIVE_BROWSER_ARTIFACTS", raising=False)
    else:
        monkeypatch.setenv("WORKER_ALLOW_SENSITIVE_BROWSER_ARTIFACTS", value)
    assert sensitive_browser_artifacts_enabled() is False
    monkeypatch.setenv("WORKER_ALLOW_SENSITIVE_BROWSER_ARTIFACTS", "1")
    assert sensitive_browser_artifacts_enabled() is True


def test_trace_context_never_starts_or_stops_in_production(monkeypatch):
    calls = []

    class Tracing:
        async def start(self, **options):
            calls.append(("start", options))

        async def stop(self):
            calls.append(("stop", None))

    page = SimpleNamespace(context=SimpleNamespace(tracing=Tracing()))
    monkeypatch.setenv("DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("WORKER_ALLOW_SENSITIVE_BROWSER_ARTIFACTS", "1")
    async def exercise():
        async with sensitive_tracing_context(page):
            pass

    asyncio.run(exercise())
    assert calls == []


def test_trace_context_pairs_start_and_stop_on_success_and_exception(monkeypatch):
    calls = []

    class Tracing:
        async def start(self, **options):
            calls.append(("start", options))

        async def stop(self):
            calls.append(("stop", None))

    page = SimpleNamespace(context=SimpleNamespace(tracing=Tracing()))
    monkeypatch.setenv("DEPLOYMENT_ENV", "development")
    monkeypatch.setenv("WORKER_ALLOW_SENSITIVE_BROWSER_ARTIFACTS", "1")
    async def success():
        async with sensitive_tracing_context(page):
            pass

    async def failure():
        async with sensitive_tracing_context(page):
            raise RuntimeError("test")

    asyncio.run(success())
    with pytest.raises(RuntimeError):
        asyncio.run(failure())
    assert [name for name, _ in calls] == ["start", "stop", "start", "stop"]


def test_trace_context_pairs_start_and_stop_on_exception(monkeypatch):
    calls = []

    class Tracing:
        async def start(self, **options):
            calls.append(("start", options))

        async def stop(self):
            calls.append(("stop", None))

    page = SimpleNamespace(context=SimpleNamespace(tracing=Tracing()))
    monkeypatch.setenv("DEPLOYMENT_ENV", "development")
    monkeypatch.setenv("WORKER_ALLOW_SENSITIVE_BROWSER_ARTIFACTS", "1")
    async def exercise():
        async with sensitive_tracing_context(page):
            raise RuntimeError("test")

    with pytest.raises(RuntimeError):
        asyncio.run(exercise())
    assert [name for name, _ in calls] == ["start", "stop"]


def test_boundary_check_forbids_direct_tracing_outside_diagnostics(tmp_path):
    source = tmp_path / "portal.py"
    source.write_text(
        "async def trace(page):\n"
        "    await page.context.tracing.start(screenshots=True)\n"
        "    await page.context.tracing.stop()\n",
        encoding="utf-8",
    )

    violations = boundary_violations(source)

    assert len([value for value in violations if "direct browser tracing" in value]) == 2


def test_portal_output_suppression_does_not_emit_context_credentials(capsys):
    with suppress_portal_output():
        print("ada-login alternate-login portal-password alternate-password")

    captured = capsys.readouterr()
    output = captured.out + captured.err
    for secret in ("ada-login", "alternate-login", "portal-password", "alternate-password"):
        assert secret not in output
