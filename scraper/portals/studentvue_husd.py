from __future__ import annotations
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup
import re

from scraper.portals.base import PortalEngine
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from playwright.async_api import TimeoutError as PlaywrightTimeout

@register_portal("studentvue_husd")
class ParentVUE_HUSD(PortalEngine):
    LOGIN = "https://parentvue.husd.org/PXP2_Login_Student.aspx?regenerateSessionId=true"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        # 1) Load login page
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(400)

        # 2) Fill & submit
        await self.page.fill("#ctl00_MainContent_username", self.sid)
        await self.page.fill("#ctl00_MainContent_password", self.pw)
        await self.page.click("#ctl00_MainContent_Submit1")

        # 3) Post-login settle without relying on networkidle
        # 15 second pause more than suffices
        await self.page.wait_for_timeout(8000)

        # 4) Navigate to gradebook using that AGU
        grade_url = f"https://parentvue.husd.org/PXP2_Gradebook.aspx"
        print(f"[PARENTVUE] Navigating to Gradebook → {grade_url}")
        await self.page.goto(grade_url, wait_until="domcontentloaded")
        # give it a moment to render
        await self.fetch_grades()
        
    async def fetch_grades(self) -> Dict[str, Any]:
        html = await self.page.content()
        parsed = self._parse_gradebook(html)
        return {"parsed_grades": parsed}
    
    def _parse_gradebook(self, html: str) -> Dict[str, Any]:
        """
        Given the Gradebook HTML, extract each course + percentage (float)
        or letter (str), preferring % over letter.
        """
        soup = BeautifulSoup(html, "html.parser")
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

        print(f"[PARENTVUE] Parsed {len(results)} courses: {list(results.items())[:3]}")
        return results

    async def logout(self) -> None:
        await self.page.wait_for_timeout(300)
