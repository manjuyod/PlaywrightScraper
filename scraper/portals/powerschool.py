from __future__ import annotations
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup
import re

from scraper.portals.base import PortalEngine
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .utils import canonicalize_course_title, universal_login_flow, wait_after_nav
DASHES = r"[\u2010-\u2015]"  # hyphen–emdash range

@register_portal("powerschool")
class PowerSchool(PortalEngine):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        username_selector = "#fieldAccount"
        password_selector = "#fieldPassword"

        await universal_login_flow(
            self.page,
            self.login_url,
            self.sid,
            self.pw,
            username_selector,
            password_selector,
            microsoft_callback=self.microsoft_login
        )

        await wait_after_nav(self.page, wait_after_load=3000)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        # grab full HTML
        html = await self.page.content()
        parsed = self._parse_gradebook(html)
        print(parsed)
        return {"parsed_grades": parsed}

    @staticmethod
    def _parse_gradebook(html: str) -> Dict[str, Any]:
        """
        Parse PowerSchool LTS table rows into { course_name: value }.
        Prefers the last <a class="bold">…</a> in each row (current term).
        Percentage → float, else letter.  N/A → "".
        """
        soup = BeautifulSoup(html, "html.parser")
        results: Dict[str, Any] = {}
        table_selector = "tr[id^=ccid_]"
        # Select each student row by id starting with ccid_
        table = soup.select(table_selector)
        for course in table:
            title_elem = course.select_one("td.table-element-text-align-start")
            if not title_elem:
                continue

            title = title_elem.get_text(strip=True)
            if 'placeholder' in title.lower():
                continue
            truncate_on = "Email"
            title = canonicalize_course_title(title, truncate_on=truncate_on)

            cols = course.select("td")[:-2] # exclude the absences and tardies rows
            grade: float | None = None
            for col in reversed(cols): # make sure we grab the most recent grade
                grades_text = col.get_text(separator='\n', strip=True)
                grades = grades_text.splitlines()
                if title in grades_text: # there may not be a grade here, bail
                    break
                if len(grades) == 2:
                    m = re.search(r"\d+(?:\.\d+)?", grades[1])  # handles 87 / 87.5 / 87%
                    grade = float(m.group(0)) if m else ("" if grades[0].upper() in ("N/A", "-", "") else grades[0])
                    break
            if grade:
                results[title] = grade
        return results
