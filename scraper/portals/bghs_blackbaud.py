# scraper/portals/blackbaud_student_bghs.py
from __future__ import annotations
import asyncio
import logging
import pathlib
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List

import re
from bs4 import BeautifulSoup  # type: ignore
from playwright.async_api import Locator, Page, Error  # type: ignore
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log
)

from .base import PortalEngine
from . import register_portal

logger = logging.getLogger("bghs_blackbaud")
logger.setLevel(logging.INFO)
@register_portal("bghs_blackbaud")
class BlackbaudBGHS(PortalEngine):
    """Blackbaud (Bishop Gorman HS) portal scraper."""

    LOGIN_URL = "https://bishopgorman.myschoolapp.com/app/student?svcid=edu#login"
    GRADES_URL = "https://bishopgorman.myschoolapp.com/app/student?svcid=edu#studentmyday/progress"

    # ── LOGIN ─────────────────────────────────────────────────────────────────
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=3, max=15),
        retry=retry_if_exception_type(Exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,  # <- expose inner exception instead of RetryError
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try:
            await self.page.context.tracing.start(screenshots=True, snapshots=True)
            print("[BBG] starting login()")
            # Entry page (Blackbaud SSO landing)
            await self.page.goto(self.LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)
            await self.page.wait_for_load_state()
            await self.page.wait_for_timeout(1000)
            await self.page.get_by_text("BBID Login Screen").click()
            await self.page.wait_for_load_state()
            await self.page.wait_for_timeout(1000)
            await self.page.locator("#google-continue-button").click()
            await self.page.wait_for_load_state()
            await self.page.wait_for_timeout(1000)
            
            # GOOGLE SIGN-IN FLOW
            # enter email
            await self.page.fill("input#identifierId", self.sid)
            await self.page.wait_for_timeout(3000)
            await self.page.get_by_text("Next").click()
            await self.page.wait_for_selector('input[name="Passwd"]')
            await self.page.fill('input[name="Passwd"]', self.pw)
            await self.page.wait_for_timeout(2000)
            await self.page.get_by_role("button", name="Next").click() # click next
            if "resourceboard" not in self.page.url:
                await self.page.locator("#primary-button").click()
            await self.page.wait_for_load_state()
            await self.page.wait_for_timeout(5000)
            
        except Exception as e:
            print(e)
            raise e
        finally:
            # await self.page.pause()
            await self.page.context.tracing.stop()

    # ── FETCH ────────────────────────────────────────────────────────────────
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=3, max=15),
        retry=retry_if_exception_type(Exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """Navigate to My Day → Progress, collect per-course grades via modal."""
        await self.page.wait_for_load_state()
        # Go directly to Progress (faster & consistent)
        await self.page.goto(self.GRADES_URL, wait_until="domcontentloaded", timeout=60_000)
        await self.page.wait_for_load_state()
        await self.page.wait_for_timeout(2000)
        soup = await self.getSoup()
        coursesTable = soup.find("div", id="coursesContainer")
        courses = coursesTable.select("div.row")
        courses_dict = {}
        # print(f"Course: {courses[0]}")
        for course in courses:
            # print(course)
            course_name: str = course.find("h3").text
            course_grade: str = course.find("h3", class_="showGrade").text
            course_grade = float(course_grade.replace("%", "").strip())
            course_name = course_name[:course_name.index("-")]
            courses_dict[course_name] = course_grade
            print(course_name, course_grade)
        print(courses_dict)
        return {"parsed_grades": courses_dict}

    # ── PARSERS ──────────────────────────────────────────────────────────────
   
