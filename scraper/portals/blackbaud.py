# scraper/portals/blackbaud_student_bghs.py
from __future__ import annotations
from playwright.async_api import expect
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type
)

from .base import (
    GradeTableConfig,
    GradeMap,
    PortalEngine,
    PlaywrightTimeout,
    UniversalLoginConfig,
)
from .utils import (
    exists,
    log_retry,
    wait_after_nav,
)

class Blackbaud(PortalEngine):
    """Blackbaud portal scraper."""

    portal_key = "blackbaud"
    url_patterns = ("myschoolapp", "blackbaud")
    login_config = UniversalLoginConfig(
        username_selector="#Username",
        password_selector="",
        sso_entry_selector="#sso-continue-button",
        google_sso=True,
        pre_fill_wait=3000,
        post_fill_wait=2000,
    )
    grade_table_config = GradeTableConfig(
        table_selector="#coursesContainer div.row",
        title_selector="h3",
        grade_selector=".showGrade",
        truncate_title_on="-",
    )

    async def after_login(self, first_name: str | None) -> None:
        _ = first_name
        await wait_after_nav(
            self.page, pattern="**/app/**", wait_after_load=5000
        )

    async def nav_to_grades(self):
        try:
            await self.page.wait_for_selector("#coursesContainer", timeout=6000)
        except PlaywrightTimeout:
            my_day_tab = self.page.get_by_role('link', name='My Day')
            grades_tab = self.page.locator("#topnav-containter").get_by_role("link", name="Progress")
            if not await exists(my_day_tab):
                await self.page.locator('#site-switcher-change').click()
                await self.page.get_by_role('link', name='Student').click()
                await self.page.wait_for_load_state()
                await self.page.wait_for_timeout(2000)
                await expect(my_day_tab).to_be_visible()
                grades_tab = self.page.locator("#topnav-containter").get_by_role("link", name="Progress")

            await my_day_tab.click()
            await grades_tab.click()
            await wait_after_nav(self.page, pattern='**/progress**', wait_after_load=2000)
    # ── FETCH ────────────────────────────────────────────────────────────────
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=3, max=15),
        retry=retry_if_exception_type(PlaywrightTimeout),
        before_sleep=log_retry,
        reraise=True,
    )
    async def fetch_grades(self) -> GradeMap:
        """Navigate to My Day → Progress, collect per-course grades via modal."""
        try:
            await self.nav_to_grades()
            parsed = await super().fetch_grades()
        except Exception as e:
            self.logger.error(
                "portal.fetch.failed", extra={"exception_type": type(e).__name__}
            )
            raise
        self.logger.info(
            "portal.fetch.completed", extra={"course_count": len(parsed)}
        )
        return parsed

    # ── PARSERS ──────────────────────────────────────────────────────────────
