from __future__ import annotations

import os
from contextlib import asynccontextmanager

from playwright.async_api import Page


def sensitive_browser_artifacts_enabled() -> bool:
    production = os.getenv("DEPLOYMENT_ENV", "").strip().lower() == "production"
    opted_in = os.getenv("WORKER_ALLOW_SENSITIVE_BROWSER_ARTIFACTS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    return not production and opted_in


@asynccontextmanager
async def sensitive_tracing_context(page: Page):
    started = sensitive_browser_artifacts_enabled()
    if started:
        await page.context.tracing.start(
            screenshots=True,
            snapshots=True,
            sources=False,
        )
    try:
        yield started
    finally:
        if started:
            await page.context.tracing.stop()
