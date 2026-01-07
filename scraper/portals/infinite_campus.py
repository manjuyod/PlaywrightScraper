from __future__ import annotations

from typing import Any, Optional

from playwright.async_api import Page
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from . import register_portal  # helper we'll create in __init__.py
from .base import PortalEngine, PlaywrightTimeout
from .utils import *


@register_portal("infinite_campus")
class InfiniteCampus(PortalEngine):
    """Portal scraper for Infinite Campus."""

    # username_field_id = 'username'
    # password_field_id = 'password'
    # ---------------------- LOGIN (home only) ----------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Only log in and arrive on the parent/home shell."""
        print(f"searching for {first_name}")
        username_selector = '#username'
        password_selector = '#password'
        try:
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                username_selector,
                password_selector,
                microsoft_callback=self.microsoft_login,
                google_callback=self.google_login
            )
            await wait_after_nav(
                self.page,
                pattern='**/nav-wrapper**',
                wait_after_load=2000,
                wait_until='networkidle'
            )

            await self.raise_login_error_if('nav-wrapper' not in self.page.url)

            print("Successfully reached the home page")
            await self.select_student(first_name, self.page) # select for student if necessary
            print("[IC] Logged in and on student/home.")
        except self.LoginError as e:
            print(e)
            raise
        finally:
            await self.page.context.tracing.stop()
    # helper
    @staticmethod
    async def select_student(first_name: str, page: Page):
        frame = page.frame(name="main-workspace")
        try:  # click the student with first name if it exists
            await frame.get_by_role('link', name=first_name).click(timeout=2000)
        except PlaywrightTimeout:
            pass  # no alternate student

    # ---------------------- NAV TO GRADES -------
    async def nav_to_grades(self):
        grades_url_pattern = "**/grades*"
        menu_selector = "#menu-toggle-button"
        grades_button_label = "Grades"
        try: # are we already on the page?
            await expect(self.page).to_have_url(grades_url_pattern)
        except AssertionError: # if not then navigate to it
            await self.page.wait_for_selector(menu_selector)
            await self.page.locator(menu_selector).click()
            await self.page.get_by_role("link", name=grades_button_label).click()
            await self.page.wait_for_url(grades_url_pattern, timeout=20000)
            await self.page.wait_for_load_state("networkidle")
    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> dict[Any, Any] | None:
        """Collect grades from the grade tab"""
        await self.page.wait_for_load_state()
        await self.page.wait_for_timeout(1500)
        # get grades
        try:
            # 0) ensure we are on the grades page and targeting the right timeframe
            frame_selector = "main-workspace"
            await self.nav_to_grades()
            frame = self.page.frame(name=frame_selector)
            # target the correct timeframe
            current_quarter = 3 # todo: make this dynamic?
            await self.select_current_quarter(current_quarter, frame)

            # collect grades
            table_selector = "div.collapsible-card.grades__card"
            course_selector = "h4 a"
            grades_selector = ".grading-score div"

            return await grades_table_to_dict(
                self.page,
                table_selector,
                course_selector,
                grades_selector,
                frame_selector=frame_selector,
                use_soup=False
            )
        #
        #     # 2) select cards
        #     await frame.wait_for_selector(table_selector, timeout=15000)
        #     cards = await frame.query_selector_all(table_selector)
        #     print(f"{len(cards)} cards found:")
        #
        #     # 3) now try to parse the table
        #         # no soup, angular sucks
        #     parsed_dict = {}
        #     for card in cards:
        #         course_elem = await card.query_selector(course_selector)
        #         grade_elems = await card.query_selector_all(grades_selector)
        #         if len(grade_elems) == 0:
        #             print("no class info")
        #             continue # no class info
        #
        #         percent_text: str | None = None
        #         for elem in reversed(grade_elems):
        #             text = (await elem.inner_text()).strip()
        #             if "%" in text:
        #                 percent_text = text
        #                 break
        #
        #         if percent_text is None:
        #             print("no percentage grade found")
        #             continue
        #
        #         course = await course_elem.inner_text()
        #         grade = canonicalize_grade(percent_text)
        #         if grade:
        #             parsed_dict[course] = grade
        #         else: # NaN grade
        #             continue
        #     print(parsed_dict)
        #     return parsed_dict
        #
        # except Exception as e:
        #     print(f"{type(e)}: {e}")
        finally:
            print("finished fetching")

    @staticmethod
    async def select_current_quarter(current_quarter: int, frame: Frame) -> None:
        # target_prefix = "Q" if "chandleraz" in self.page.url else "QT"

        # target_quarter_tag = target_prefix + str(current_quarter)
        prefix = "QT"
        backup_prefix = "Q"
        try:
            qt = frame.get_by_role("button", name=prefix + str(current_quarter))
            if not await exists(qt):
                qt = frame.get_by_role("button", name=backup_prefix + str(current_quarter))
            assert await exists(qt)
            await qt.wait_for(timeout=1000)
            await qt.click()


        except PlaywrightTimeout:
            prefix = backup_prefix
            pass
    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        # await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
