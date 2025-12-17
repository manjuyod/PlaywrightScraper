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
                wait_after_load=3000,
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
            await frame.get_by_role('link', name=first_name).click(timeout=3000)
        except PlaywrightTimeout:
            pass  # no alternate student
    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> dict[str, dict[Any, Any]] | None:
        """Collect grades from the grade tab"""
        await self.page.wait_for_load_state()
        await self.page.wait_for_timeout(1500)
        # get grades
        try:
            await self.page.wait_for_selector('#menu-toggle-button')
            # nav to the grades page
            await self.page.locator("#menu-toggle-button").click()
            await self.page.get_by_role("link", name='Grades').click()
            await self.page.wait_for_url("**/grades*", timeout = 20000)
            await self.page.wait_for_load_state("networkidle")
            frame = self.page.frame(name="main-workspace")
            # target the correct timeframe
            if "chandleraz" in self.page.url:
                await frame.get_by_role("button", name="Q2").click()
            else:
                try:
                    qt2 = frame.get_by_role("button", name="QT2")
                    await qt2.wait_for(timeout = 1000)
                    await qt2.click()
                except PlaywrightTimeout:
                    pass
            # collect grades
            await frame.wait_for_selector("div.collapsible-card.grades__card", timeout=15000)
            cards = await frame.query_selector_all("div.collapsible-card.grades__card")
            print(f"{len(cards)} cards found:")
            # no soup, angular sucks
            # now try to parse the page
            parsed_dict = {}
            for card in cards:
                course_elem = await card.query_selector("h4 a")
                course = await course_elem.inner_text()
                # print(f"\nCourse: {course}\n")

                grade_elems = await card.query_selector_all(".grading-score div")
                if len(grade_elems) == 0:
                    print("no class info")
                    continue # no class info

                percent_text: str | None = None
                for elem in reversed(grade_elems):
                    text = (await elem.inner_text()).strip()
                    if "%" in text:
                        percent_text = text
                        break

                if percent_text is None:
                    print("no percentage grade found")
                    continue

                course = await course_elem.inner_text()
                grade_str = percent_text
                try:
                    grade = float(grade_str
                                  .replace("(", "")
                                  .replace(")", "")
                                  .replace("%", ""))
                except ValueError: # not a number grade
                    continue 
                parsed_dict[course] = grade

            print(parsed_dict)
            # await self.page.pause()
            return {
                "parsed_grades": parsed_dict
            }
        except Exception as e:
            print(f"{type(e)}: {e}")
        finally:
            print("finished fetching")
            await self.page.context.tracing.stop()
            # await self.page.pause()
    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        # await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
