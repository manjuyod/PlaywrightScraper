from __future__ import annotations
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup
import re

from scraper.portals.base import PortalEngine, PlaywrightTimeout
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from .utils import *

@register_portal("parentvue")
class ParentVUE(PortalEngine):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try:
            username_selector = '#ctl00_MainContent_username'
            password_selector = '#ctl00_MainContent_password'
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                username_selector,
                password_selector,
                None,
                None
            )
            await self.raise_login_error_if('Login' in self.page.url)  # we should move past the login screen after clicking the login button
            await wait_after_nav(self.page, wait_until='domcontentloaded', timeout=30000)

            # ensure that we select the correct student if there may be multiple
            if 'Login_Parent' in self.login_url:
                await self.select_student(first_name)
            # nav to grades page given that we are on the home page
            print(f"[PARENTVUE] Reached Home Page; Navigating to Gradebook for {first_name}")
            await self.page.get_by_role("listitem").filter(has_text="Grade Book").click()
            await self.page.wait_for_load_state(state='domcontentloaded', timeout=30000)
        except Exception as e:
            print(e)
            raise
        finally:
            await self.page.context.tracing.stop()
            # await self.page.pause()


    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def select_student(self, first_name: Optional[str] = None):
        """
        Open the student dropdown, click the matching student,
        grab its data-agu, then fetch and return the Gradebook HTML.
        """
        target = (first_name or getattr(self, "student_name", "") or "").strip()
        if not target:
            raise RuntimeError("No first_name provided to select_student()")
        print(f'[PARENTVUE] Selecting student {target}]')
        target_lc = target.lower()

        # base selectors
        header_sel = "#ctl00_ctl00_MainContent_PXPHeader"
        selector_root = f"{header_sel} #ctl00_ctl00_MainContent_StudentSelector"
        current_button = f"{selector_root} .current"
        menu_ul = f"{selector_root} ul.dropdown-menu"
        item_info = f"{menu_ul} .student-info"
        current_name_sel = f"{selector_root} .current .student-name"

        # 1) If we're already on the right student, bail and grab its AGU
        try:
            name = (await self.page.locator(current_name_sel).inner_text()).strip()
            if target_lc in name.lower():
                # find its data-agu in the .current container
                agu = await self.page.locator(f"{selector_root} .current .student-info").get_attribute("data-agu")
                if not agu:
                    raise RuntimeError("Could not read data-agu from current student")
                print(f"[PARENTVUE] Already on {name}; abort selection")
                return
        except Exception:
            # continue to the dropdown approach
            pass

        # 2) Open the dropdown
        await self.page.click(current_button)
        await self.page.wait_for_selector(menu_ul, timeout=5000)

        # 3) Find and click the matching student-info
        items = self.page.locator(item_info)
        n = await items.count()
        if n == 0:
            raise RuntimeError("No student items in dropdown")

        agu = None
        for i in range(n):
            info = items.nth(i)
            name = (await info.locator(".student-name").inner_text()).strip()
            if target_lc in name.lower():
                agu = await info.get_attribute("data-agu")
                await info.click()
                print(f"[PARENTVUE] Clicked student '{name}' (AGU={agu})")
                break
        if not agu:
            raise RuntimeError(f"No dropdown student matched '{target}'")

    async def fetch_grades(self) -> Dict[str, Any]:
        soup = await self.get_soup()
        parsed = self._parse_gradebook(soup)
        return {"parsed_grades": parsed}

    @staticmethod
    def _parse_gradebook(soup: BeautifulSoup) -> Dict[str, Any]:
        """
        Given the Gradebook HTML, extract each course + percentage (float)
        or letter (str), preferring % over letter.
        """
        results: Dict[str, Any] = {}

        # 1) Find each header row for a class
        headers = soup.select("div.gb-class-header.gb-class-row")
        for header in headers:
            # a) extract the course title text, e.g. "1: Chemistry"
            btn = header.find("button", class_="course-title")
            if not btn:
                continue
            raw = btn.get_text(strip=True)
            # strip off leading "number: " if present
            course = re.sub(r"^\d+:\s*", "", raw)

            # b) find the very next row containing the marks/scores
            row = header.find_next_sibling(
                lambda tag: tag.name == "div" and "gb-class-row" in tag.get("class", [])
                                                    and "gb-class-header" not in tag.get("class", [])
            )
            if not row:
                continue

            # c) look for a <span class="score">63.5%</span>
            score_tag = row.find("span", class_="score")
            mark_tag = row.find("span", class_="mark")

            value: Any = None
            if score_tag and "%" in score_tag.text:
                txt = score_tag.text.strip().rstrip("%")
                try:
                    value = float(txt)
                except ValueError:
                    value = txt  # fallback to raw string
            elif mark_tag:
                value = mark_tag.text.strip()

            if isinstance(value, str):
                if value.strip().lower() in {"n/a", "na", "none", "null", ""}:
                    value = ""

            # only include if we actually got something (float or non-empty string)
            if value is not None and not (isinstance(value, str) and value == ""):
                results[course] = value

        print(f"[PARENTVUE] Parsed {len(results)} courses: {list(results.items())}")
        return results

    async def logout(self) -> None:
        await self.page.wait_for_timeout(300)
