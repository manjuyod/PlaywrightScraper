from __future__ import annotations

from typing import Any, Optional

from playwright.async_api import Page
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from . import register_portal  # helper we'll create in __init__.py
from .base import PortalEngine, PlaywrightTimeout


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
        print(f"searching for {first_name}")
        #TODO: Insert LoginError logic
        try:
            await self.page.goto(self.login_url, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(500)
            await self.page.fill("#username", self.sid)
            await self.page.fill("#password", self.pw)
            await self.page.wait_for_timeout(1000) # give time between input and continue
            await self.page.get_by_role('button', name="Log In").click()
            await self.page.wait_for_load_state('networkidle')
            await self.page.wait_for_timeout(1000)

            await self.raise_if_login_error('nav-wrapper' not in self.page.url)

            print("Successfully reached the home page")
            await self.page.wait_for_load_state(timeout=10000)
            await self.page.wait_for_timeout(1500)
            await self.select_student(first_name, self.page) # select for student if necessary
            print("[IC] Logged in and on student/home.")
        except Exception as e:
            print(e)
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
            if "chandleraz" in self.page.url:
                await frame.get_by_role("button", name="Q2").click()
            else:
                try:
                    qt2 = frame.get_by_role("button", name="QT2")
                    await qt2.wait_for(timeout = 1000)
                    await qt2.click()
                except PlaywrightTimeout:
                    pass
            await frame.wait_for_selector("div.collapsible-card.grades__card", timeout=15000)
            cards = await frame.query_selector_all("div.collapsible-card.grades__card")
            print(f"{len(cards)} cards found:")
            # no soup, angular sucks
            # now try to parse the page
            parsed_dict = {}
            for card in cards:
                # print(f"Class: {card}\n\n")
                course_elem = await card.query_selector("h4 a")
                grade_elem = await card.query_selector_all(".grading-score div")
                # print(grade_elem)
                if len(grade_elem) == 0:
                    print("no class info") 
                    continue # no class info
                course = await course_elem.inner_text()
                grade_index = -2 # the percentage grade is almost always the second to last element
                grade_str: str = await grade_elem[grade_index].inner_text() 
                # print(f"course: {course} grade: {grade_str}") # debug
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
