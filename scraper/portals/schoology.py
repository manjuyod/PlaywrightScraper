from __future__ import annotations
from scraper.portals.base import (
    GradeTableConfig,
    GradeMap,
    PortalEngine,
    PlaywrightTimeout,
    UniversalLoginConfig,
)
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .utils import wait_after_nav
class Schoology(PortalEngine):
    portal_key = "schoology"
    url_patterns = ("schoology",)
    login_config = UniversalLoginConfig(
        username_selector="#edit-mail",
        password_selector="#edit-pass",
        sso_entry_selector='a[href*="Student"]',
        microsoft_sso=True,
    )
    grade_table_config = GradeTableConfig(
        table_selector="div[id^='s-js-gradebook-course']",
        title_selector=".gradebook-course-title",
        grade_selector="course-grade-value",
        truncate_title_on=":",
    )

    async def after_login(self, first_name: str | None) -> None:
        _ = first_name
        await wait_after_nav(self.page, wait_after_load=5000)
        await self.page.get_by_role("button", name="Grades").click()
        await self.page.wait_for_timeout(1000)
        await self.page.get_by_text("Grade Report").click()
        await self.page.wait_for_timeout(3000)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> GradeMap:
        # verify that we reached the grades page
        if 'grades' not in self.page.url:
            raise self.LoginError('No grades page')

        return await super().fetch_grades()
