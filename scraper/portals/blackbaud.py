# scraper/portals/blackbaud_student_bghs.py
from __future__ import annotations
import asyncio
import logging
import pathlib
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List

import re
from bs4 import BeautifulSoup  # type: ignore
from playwright.async_api import Locator, Page, Error, expect
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log
)

from .base import PortalEngine, PlaywrightTimeout
from . import register_portal

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
            await self.page.context.tracing.start(screenshots=True, snapshots=True)
            print("[BBG] starting login()")
            # Entry page (Blackbaud SSO landing)
            await self.page.goto(self.login_url, wait_until="domcontentloaded", timeout=45_000)
            await self.page.wait_for_load_state()


            await self.page.fill("#Username", self.sid)
            await self.page.get_by_role('button', name="Next").click()
            await self.page.wait_for_load_state()
            await self.page.wait_for_timeout(1000)
            await self.google_signin()

            await self.page.wait_for_load_state()
            await self.page.wait_for_timeout(5000)
        except Exception as e:
            print(e)
            raise e
        finally:

            await self.page.context.tracing.stop()

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
        try:
            await self.page.wait_for_load_state()
            await self.page.wait_for_timeout(2000)
            # Go directly to Progress (faster & consistent)
            # await self.page.goto(self.GRADES_URL, wait_until="domcontentloaded", timeout=60_000)
            if 'progress' not in self.page.url:
                my_day_tab = self.page.get_by_role('link', name='My Day')
                grades_tab = self.page.locator("#topnav-containter").get_by_role("link", name="Progress")
                try:
                    await expect(my_day_tab).to_be_visible()
                except AssertionError:
                    await self.page.locator('#site-switcher-change').click()
                    await self.page.get_by_role('link', name='Student').click()
                    await self.page.wait_for_load_state()
                    await self.page.wait_for_timeout(2000)
                    await expect(my_day_tab).to_be_visible()
                    grades_tab = self.page.locator("#topnav-containter").get_by_role("link", name="Progress")

                await my_day_tab.click()
                await grades_tab.click()
                await self.page.wait_for_load_state()
                await self.page.wait_for_timeout(2000)

            soup = await self.get_soup()
            courses_table = soup.find("div", id="coursesContainer")
            courses = courses_table.select("div.row")
            parsed = {}
            # print(f"Course: {courses[0]}")
            for course in courses:
                # print(course)
                course_name_raw: str = course.find("h3").text
                course_name = course_name_raw[:course_name_raw.index("-")]

                course_grade_str: str = course.find("h3", class_="showGrade").text
                course_grade = float(course_grade_str.replace("%", "").strip())

                parsed[course_name] = course_grade
                # print(course_name, course_grade)
            print(parsed)
            return {"parsed_grades": parsed}
        except Exception as e:
            print(e)
        finally:
            pass
            # await self.page.pause()

    # ── PARSERS ──────────────────────────────────────────────────────────────
   
