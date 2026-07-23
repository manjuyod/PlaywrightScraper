from __future__ import annotations
from typing import Any, Dict, Optional
from scraper.portals.base import PortalEngine, PlaywrightTimeout
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .utils import grades_table_to_dict, universal_login_flow, wait_after_nav
@register_portal("schoology")
class Schoology(PortalEngine):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try:
            username_selector = '#edit-mail'
            password_selector = '#edit-pass'
            sso_login_selector = 'a[href*="Student"]'
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                username_selector,
                password_selector,
                microsoft_callback=self.microsoft_login,
                sso_login_selector=sso_login_selector
            )
            await wait_after_nav(self.page, wait_after_load=5000)

            # navigate to the grades page
            await self.page.get_by_role('button', name='Grades').click()
            await self.page.wait_for_timeout(1000) # second delay to prevent 'Too many requests' error
            await self.page.get_by_text('Grade Report').click()
            await self.page.wait_for_timeout(3000)
        except Exception as e:
            self.logger.error(
                "portal.login.failed", extra={"exception_type": type(e).__name__}
            )
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        # verify that we reached the grades page
        if 'grades' not in self.page.url:
            raise self.LoginError('No grades page')

        parsed = {}
        table_selector = "div[id^='s-js-gradebook-course']"
        title_selector = ".gradebook-course-title"
        grade_selector = "course-grade-value"
        truncate_title_on = ':'
        try:
            parsed = await grades_table_to_dict(
                self.page,
                table_selector,
                title_selector,
                grade_selector,
                truncate_title_on=truncate_title_on
            )
        finally:
            return parsed
