from __future__ import annotations
from datetime import datetime
from typing import Any, Optional

from playwright.async_api import Frame, Page, expect
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from . import register_portal  # helper we'll create in __init__.py
from .base import PortalEngine, PlaywrightTimeout
from .utils import exists, grades_table_to_dict, universal_login_flow


@register_portal("infinite_campus")
class InfiniteCampus(PortalEngine):
    """Portal scraper for Infinite Campus."""
    # ---------------------- LOGIN (home only) ----------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Only log in and arrive on the parent/home shell."""
        self.logger.info("portal.login.started")
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
            self.logger.debug("portal.login.awaiting_redirect")

            invalid_creds_msg = "Incorrect Username and/or Password"
            login_failed = await exists(self.page.get_by_text(invalid_creds_msg, exact=False))
            await self.raise_login_error_if(login_failed, "Infinite Campus login failed due to incorrect credentials")
            await self.raise_login_error_if('nav-wrapper' not in self.page.url)
            await self.page.wait_for_load_state("networkidle")
            self.logger.info("portal.login.succeeded")
            await self.select_student(first_name, self.page) # select for student if necessary
            self.logger.debug("portal.login.student_home_ready")
        except self.LoginError:
            raise
    # helper
    async def select_student(self, first_name: str | None, page: Page):
        parent = page.frame("main-workspace")
        if not parent:
            parent = page
        if not first_name:
            return
        try:  # click the student with first name if it exists
            self.logger.debug("portal.student_selection.started")
            await parent.get_by_role('link', name=first_name, exact=False).click(timeout=2000)
        except PlaywrightTimeout:
            self.logger.info("portal.student_selection.not_available")
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

    @staticmethod
    def term_semester_from_today() -> int:
        """
        Determine current academic term semester.

        Return: 1 for Fall, 2 for Spring
        """
        now = datetime.now()
        m = now.month
        if m >= 8:  # Aug–Dec → Fall of current year
            sem = 1
        elif m <= 5:  # Jan–May → Spring of previous fall year
            sem = 2
        else:  # Jun–Jul → prep for upcoming Fall
            sem = 1
        return sem

    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> dict[Any, Any]: # TODO: Alter to parse from 'All terms' instead of 'Current term'
        """Collect grades from the grade tab"""
        await self.page.wait_for_load_state()
        await self.page.wait_for_timeout(1500)
        # get grades
        try:
            # 0) ensure we are on the grades page and targeting the right timeframe
            await self.nav_to_grades()

            frame_selector = "main-workspace"
            frame = self.page.frame(frame_selector)
            assert frame is not None, "Infinite Campus main workspace frame not found"
            # target the correct timeframe




            # collect grades
            table_selector = "div.collapsible-card.grades__card"
            course_selector = "h4 a"
            grades_selector = ".grading-score div"

            # we will parse the current quarter as well as the next quarter, and keep the grades from the most recent quarter
            current_semester = self.term_semester_from_today() # either 1 or 2 (this only works if the page is using quarters not semesters)

            try: # some ic portals don't populate each quarter, so we need to handle the case where the 'next' quarter doesn't exist
                await self.select_timeframe(current_semester, frame)
                next_q_grades = await grades_table_to_dict(
                    self.page,
                    table_selector,
                    course_selector,
                    grades_selector,
                    frame_selector=frame_selector,
                    use_soup=False
                )
            except AssertionError:
                next_q_grades = {}

            await self.select_timeframe(current_semester, frame, q_before=True)
            cur_q_grades = await grades_table_to_dict(
                self.page,
                table_selector,
                course_selector,
                grades_selector,
                frame_selector=frame_selector,
                use_soup=False
            )

            # match the possible grades from the next quarter to the current quarter
            for course in cur_q_grades.keys():
                recent_grade = next_q_grades.get(course, None)
                if recent_grade:
                    cur_q_grades[course] = recent_grade

            self.logger.info(
                "portal.fetch.completed", extra={"course_count": len(cur_q_grades)}
            )
            return cur_q_grades
        except Exception as exc:
            self.logger.error(
                "portal.fetch.failed", extra={"exception_type": type(exc).__name__}
            )
            raise
        finally:
            self.logger.debug("portal.fetch.finished")

    @staticmethod
    async def select_timeframe(current_sem: int, frame: Frame, q_before = False) -> None:
        timeframe = frame.get_by_role("button", name="QT" + str(current_sem * 2 - 1 if q_before else current_sem * 2))
        if not await exists(timeframe):
            timeframe = frame.get_by_role("button", name="Q" + str(current_sem * 2 - 1 if q_before else current_sem * 2))
        if not await exists(timeframe):
            timeframe = frame.get_by_role("button", name="S" + str(current_sem if q_before else current_sem - 1))
        assert await exists(timeframe)
        await timeframe.wait_for(timeout=1000)
        await timeframe.click()

    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        # await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
