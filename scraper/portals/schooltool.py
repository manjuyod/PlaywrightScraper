from __future__ import annotations
from scraper.portals.base import (
    GradeTableConfig,
    GradeMap,
    PortalEngine,
    PlaywrightTimeout,
    UniversalLoginConfig,
)
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .utils import exists, wait_after_nav
class SchoolTool(PortalEngine):
    portal_key = "schooltool"
    url_patterns = ("schooltool",)
    login_config = UniversalLoginConfig(
        username_selector="",
        password_selector="",
        sso_entry_selector='a[id="Google;btn"]',
        microsoft_sso=True,
        google_sso=True,
        alternate_sso=True,
    )
    grade_table_config = GradeTableConfig(
        table_selector=(
            "table[id*='StudentGradesMPView_DataGrid1'] tr.DataGridItemStyle, "
            "table[id*='StudentGradesMPView_DataGrid1'] "
            "tr.DataGridAlternateItemStyle"
        ),
        title_selector="td:nth-of-type(1)",
        grade_selector="td:nth-of-type(7)",
    )

    async def alternate_sso_login(self) -> None:
        await self.microsoft_login()

    async def after_login(self, first_name: str | None) -> None:
        _ = first_name
        await wait_after_nav(
            self.page, wait_until="networkidle", wait_after_load=10000
        )
        student_record_selector = 'font-icon[title="View Student Record"]'
        if not await exists(
            self.page.locator(student_record_selector), timeout=15000
        ):
            self.logger.warning("portal.navigation.student_record_missing")
            return
        if await self.page.locator(student_record_selector).is_visible():
            await self.page.click(student_record_selector)
            self.logger.debug("portal.navigation.student_record_selected")
            await wait_after_nav(self.page, wait_after_load=5000)
            grades_selector = 'a:has-text("Grades")'
            if await exists(self.page.locator(grades_selector), timeout=5000):
                await self.page.click(grades_selector)
                self.logger.debug("portal.navigation.grades_selected")
                await wait_after_nav(self.page, wait_after_load=5000)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> GradeMap:
        try:
            table_root_selector = "table[id*='StudentGradesMPView_DataGrid1']"
            await self.page.wait_for_selector(table_root_selector, timeout=15000)

            return await super().fetch_grades()
        except Exception as e:
            self.logger.error(
                "portal.fetch.failed", extra={"exception_type": type(e).__name__}
            )
            return {}
