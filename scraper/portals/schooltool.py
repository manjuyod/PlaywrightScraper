from __future__ import annotations
from typing import Any, Dict, Optional
from scraper.portals.base import PortalEngine, PlaywrightTimeout
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .utils import exists, grades_table_to_dict, universal_login_flow, wait_after_nav
@register_portal("schooltool")
class SchoolTool(PortalEngine):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try:
            sso_login_selector = 'a[id="Google;btn"]'
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                username_selector="",
                password_selector="",
                sso_login_selector=sso_login_selector,
                microsoft_callback=self.microsoft_login,
                google_callback=self.google_login,
                alt_sso_callback=self.alt_sso_login
            )
            await wait_after_nav(self.page, wait_until='networkidle', wait_after_load=10000)
            # navigate to the student records pag
            student_record_page_selector = 'font-icon[title="View Student Record"]'
            if await exists(self.page.locator(student_record_page_selector), timeout=15000):
                if await self.page.locator(student_record_page_selector).is_visible():
                    await self.page.click(student_record_page_selector)
                    self.logger.debug("portal.navigation.student_record_selected")
                    await wait_after_nav(self.page, wait_after_load=5000)

                    # from here, nav to the grades table
                    grades_page_selector = 'a:has-text("Grades")'
                    if await exists(self.page.locator(grades_page_selector), timeout=5000):
                        await self.page.click(grades_page_selector)         
                        self.logger.debug("portal.navigation.grades_selected")
                        await wait_after_nav(self.page, wait_after_load=5000)
            else:
                self.logger.warning("portal.navigation.student_record_missing")
        except Exception as e:
            self.logger.error(
                "portal.login.failed", extra={"exception_type": type(e).__name__}
            )
            raise

    async def alt_sso_login(self):
        await self.microsoft_login()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        try:
            table_root_selector = "table[id*='StudentGradesMPView_DataGrid1']"
            await self.page.wait_for_selector(table_root_selector, timeout=15000)

            table_selector = (
                f"{table_root_selector} tr.DataGridItemStyle, "
                f"{table_root_selector} tr.DataGridAlternateItemStyle"
            )
            title_selector = "td:nth-of-type(1)"
            grade_selector = "td:nth-of-type(7)"

            return await grades_table_to_dict(
                self.page,
                table_selector,
                title_selector,
                grade_selector,
            )
        except Exception as e:
            self.logger.error(
                "portal.fetch.failed", extra={"exception_type": type(e).__name__}
            )
            return {}
