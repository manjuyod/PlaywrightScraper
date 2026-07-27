from __future__ import annotations
from bs4 import BeautifulSoup
import re
from typing_extensions import override

from scraper.portals.base import PortalEngine, UniversalLoginConfig
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .utils import canonicalize_course_title, canonicalize_grade, wait_after_nav
DASHES = r"[\u2010-\u2015]"  # hyphen–emdash range

class PowerSchool(PortalEngine):
    portal_key = "powerschool"
    url_patterns = ("powerschool",)
    login_config = UniversalLoginConfig(
        username_selector="#fieldAccount",
        password_selector="#fieldPassword",
        microsoft_sso=True,
    )

    async def after_login(self, first_name: str | None) -> None:
        _ = first_name
        await wait_after_nav(self.page, wait_after_load=3000)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
    )
    @override
    async def fetch_grades(self) -> dict[str, float]:
        self.logger.info("portal.fetch.started")
        html = await self.page.content()
        parsed = self._parse_gradebook(html)
        self.logger.info(
            "portal.fetch.completed", extra={"course_count": len(parsed)}
        )
        return parsed

    @staticmethod
    def _parse_gradebook(html: str) -> dict[str, float]:
        """
        Parse PowerSchool LTS table rows into { course_name: value }.
        Prefers the last <a class="bold">…</a> in each row (current term).
        Numeric and letter grades are normalized to percentages; unavailable
        grades are omitted.
        """
        soup = BeautifulSoup(html, "html.parser")
        results: dict[str, float] = {}
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
                    m = re.search(r"\d+(?:\.\d+)?", grades[1])
                    grade = (
                        float(m.group(0))
                        if m
                        else canonicalize_grade(grades[0])
                    )
                    break
            if grade:
                results[title] = grade
        return results
