# scraper/portals/blackbaud_student_bghs.py
from __future__ import annotations
import logging
from typing import Dict, Any, Optional
from playwright.async_api import expect
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log
)

from .base import PortalEngine, PlaywrightTimeout
from . import register_portal
from .utils import exists, wait_after_nav, universal_login_flow, grades_table_to_dict

logger = logging.getLogger("blackbaud")
logger.setLevel(logging.INFO)
@register_portal("blackbaud")
class Blackbaud(PortalEngine):
    """Blackbaud portal scraper."""

    # ── LOGIN ─────────────────────────────────────────────────────────────────
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=3, max=15),
        retry=retry_if_exception_type(PlaywrightTimeout),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,  # <- expose inner exception instead of RetryError
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try:
            print("[BBG] starting login()")
            # Entry page (Blackbaud SSO landing)
            username_selector = '#Username'
            password_selector = ''
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                username_selector,
                password_selector,
                sso_login_selector='#sso-continue-button',
                google_callback=self.google_login,
                pre_fill_wait=3000,
                post_fill_wait=2000
            )
            await wait_after_nav(self.page, pattern='**/app/**', wait_after_load=5000)

        except Exception:
            raise
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
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """Navigate to My Day → Progress, collect per-course grades via modal."""
        parsed = {}
        try:
            await self.nav_to_grades()
            table_selector = "#coursesContainer div.row"
            title_selector = 'h3'
            truncate_on = '-'
            grade_selector = '.showGrade'
            parsed = await grades_table_to_dict(
                self.page,
                table_selector,
                title_selector,
                grade_selector,
                truncate_title_on=truncate_on
            )
        except Exception as e:
            print(e)
        finally:
            print(parsed)
            return {"parsed_grades": parsed}

    # ── PARSERS ──────────────────────────────────────────────────────────────
