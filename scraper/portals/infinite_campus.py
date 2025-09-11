from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import List, Dict, Any, Optional, Tuple

from bs4 import BeautifulSoup
from playwright.async_api import Page, Frame
from .base import PortalEngine
from . import register_portal, LoginError # helper we'll create in __init__.py
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_if_not_exception_type


@register_portal("infinite_campus")
class InfiniteCampus(PortalEngine):
    """Portal scraper for Infinite Campus."""
    
    # ---------------------- LOGIN (home only) ----------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_not_exception_type(LoginError),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Only log in and arrive on the parent/home shell."""
        #TODO: Insert LoginError logic
        await self.page.goto(self.login_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(500)
        await self.page.fill("#username", self.sid)
        await self.page.fill("#password", self.pw)
        await self.page.wait_for_timeout(1000) # give time between input and continue
        await self.page.get_by_role('button', name="Log In").click()
        await self.page.wait_for_load_state('networkidle')
        await self.page.wait_for_timeout(1000)
        if 'nav-wrapper' in self.page.url:
            print("Successfully reached the home page")
        else:
            print("\t\t\tHome screen is not apparent")
            raise LoginError
        print("[IC] Logged in and on student/home.")

    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """Collect grades from the grade tab"""
        # finish nav from login
        await self.page.wait_for_load_state()
        await self.page.wait_for_timeout(1500)  # small hard wait for Angular to attach
        # get grades
        try:
            await self.page.wait_for_selector('#menu-toggle-button')
            # nav to the grades page
            await self.page.locator("#menu-toggle-button").click()
            await self.page.get_by_role("link", name='Grades').click()
            await self.page.wait_for_url("**/grades*", timeout = 20000)
            await self.page.wait_for_load_state("networkidle")
            frame = self.page.frame(name="main-workspace")
            if ("chandleraz" in  self.page.url):
                await frame.get_by_text("Q2").click()
            await frame.wait_for_selector("div.collapsible-card.grades__card", timeout=15000)
            cards = await frame.query_selector_all("div.collapsible-card.grades__card")
            print(f"{len(cards)} cards found:")
            # for card in cards: 
            #     print(card)
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
                    grade = float(grade_str.replace("(", "").replace(")", "").replace("%", ""))
                except ValueError: # not a number grade
                    continue 
                parsed_dict[course] = grade
                # print(f"course: {} grade: {await grade[1].inner_text()}")
            # Optional debug dump
            #out_dir = Path(__file__).resolve().parents[2] / "output" / "debug"
            #out_dir.mkdir(parents=True, exist_ok=True)
            #dump = out_dir / f"home-notifications-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
            #dump.write_text(html, encoding="utf-8")
            #print(f"[IC] Wrote notifications HTML → {dump}")
            print(parsed_dict)
            # like_name = (getattr(self, "student_name", None) or "").strip()
            # parsed_dict = self._parse_semester_from_notifications(html, first_name=like_name)
            # ^ parsed_dict is already {"Course": 93.4 or "A", ...}
            return {
                "parsed_grades": parsed_dict
            }
        except Exception as e:
            print(f"{type(e)}: {e}")
        finally:
            print("finished fetching") 
            # await self.page.pause()
    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
