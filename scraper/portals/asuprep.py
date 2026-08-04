from __future__ import annotations
from scraper.portals.base import (
    GradeMap,
    PortalEngine,
    PlaywrightTimeout,
    UniversalLoginConfig,
)
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .utils import wait_after_nav
class ASUPrep(PortalEngine):
    portal_key = "asuprep"
    url_patterns = ("global.asuprep",)
    login_config = UniversalLoginConfig(
        username_selector="#ctl00_centerLoginContent_tbLogin",
        password_selector="#ctl00_centerLoginContent_tbPassword",
    )

    async def after_login(self, first_name: str | None) -> None:
        _ = first_name
        await wait_after_nav(
            self.page, wait_until="networkidle", wait_after_load=10000
        )
        await self.page.click('a:has-text("Gradebook")')
        await wait_after_nav(
            self.page, wait_until="networkidle", wait_after_load=5000
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> GradeMap:
        return {}
