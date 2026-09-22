from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlsplit

from playwright.async_api import TimeoutError as PlaywrightTimeout
from typing_extensions import override

from .base import GradeMap, PortalEngine
from .clever_entry import https_origin, is_clever_entry


_CLEVER_ORIGIN = "https://clever.com"
_GOOGLE_ORIGIN = "https://accounts.google.com"


def is_student_dashboard(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        https_origin(url) == _CLEVER_ORIGIN
        and parsed.path.startswith("/in/")
        and parsed.path.endswith("/student/portal")
    )


class Clever(PortalEngine):
    """Authenticate a student into the Clever application dashboard."""

    portal_key: ClassVar[str] = "clever"
    url_patterns: ClassVar[tuple[str, ...]] = ("clever.com/oauth/authorize",)

    def _require_origin(self, origin: str) -> None:
        if https_origin(self.page.url) != origin:
            raise self.LoginError("portal login rejected")

    @override
    async def login(self, first_name: str | None = None) -> None:
        _ = first_name
        if not is_clever_entry(self.login_url):
            raise self.LoginError("portal login rejected")
        try:
            _ = await self.page.goto(
                self.login_url,
                wait_until="domcontentloaded",
                timeout=15_000,
            )
            if not is_clever_entry(self.page.url):
                raise self.LoginError("portal login rejected")

            await self.page.get_by_role("link", name="Google", exact=True).click()
            await self.page.wait_for_url(
                "https://accounts.google.com/**",
                wait_until="domcontentloaded",
                timeout=15_000,
            )
            self._require_origin(_GOOGLE_ORIGIN)
            await self.page.locator("input#identifierId").fill(self.sid)
            self._require_origin(_GOOGLE_ORIGIN)
            await self.page.get_by_role("button", name="Next").click()
            password = self.page.locator('input[name="Passwd"]')
            await password.wait_for(state="visible", timeout=15_000)
            self._require_origin(_GOOGLE_ORIGIN)
            await password.fill(self.pw)
            self._require_origin(_GOOGLE_ORIGIN)
            await self.page.get_by_role("button", name="Next").click()
            await self.page.wait_for_url(
                is_student_dashboard,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            if not is_student_dashboard(self.page.url):
                raise self.LoginError("portal login rejected")
        except self.LoginError:
            raise
        except PlaywrightTimeout:
            raise self.LoginError("portal login rejected") from None
        self.logger.info("portal.login.succeeded")

    @override
    async def fetch_grades(self) -> GradeMap:
        raise RuntimeError("clever_downstream_portal_not_configured")
