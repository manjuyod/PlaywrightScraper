from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup
import unicodedata
import re

from scraper.portals.base import PortalEngine
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

DASHES = r"[\u2010-\u2015]"  # hyphen–emdash range

def canonicalize_course(text: str) -> str:
    """
    Normalize Unicode, convert NBSP to space, unify dashes to '-',
    collapse whitespace. Does NOT drop prefixes/suffixes.
    """
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("\xa0", " ")
    t = re.sub(DASHES, "-", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

@register_portal("powerschool_lts_parent")
class PowerSchoolLTSParent(PortalEngine):
    LOGIN = "https://lts.powerschool.com/public/home.html"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        # 1) Load login page
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(500)

        # 2) Fill & submit
        await self.page.fill("#fieldAccount", self.sid)
        await self.page.fill("#fieldPassword", self.pw)
        await self.page.click("#btn-enter-sign-in")

        # 3) Give it time to load the gradebook table
        await self.page.wait_for_timeout(8000)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        # grab full HTML
        html = await self.page.content()
        parsed = self._parse_gradebook(html)
        return {"parsed_grades": parsed}

    def _parse_gradebook(self, html: str) -> Dict[str, Any]:
        """
        Parse PowerSchool LTS table rows into { course_name: value }.
        Prefers the last <a class="bold">…</a> in each row (current term).
        Percentage → float, else letter.  N/A → "".
        """
        soup = BeautifulSoup(html, "html.parser")
        results: Dict[str, Any] = {}

        # Select each student row by id starting with ccid_
        for tr in soup.select("tr[id^=ccid_]"):
            course_td = tr.select_one("td.table-element-text-align-start")
            if not course_td:
                continue

            # get raw text, then SANITIZE once
            course_raw = course_td.get_text(" ", strip=True)
            course = canonicalize_course(course_raw)   # <-- use sanitized

            bold_links = tr.select("a.bold")
            if not bold_links:
                results[course] = ""
                continue

            link = bold_links[-1]
            text = link.get_text("\n", strip=True)
            parts = text.splitlines()

            value: Any = ""
            if len(parts) >= 2:
                m = re.search(r"\d+(?:\.\d+)?", parts[1])  # handles 87 / 87.5 / 87%
                value = float(m.group(0)) if m else ("" if parts[0].upper() in ("N/A", "-", "") else parts[0])
            else:
                letter = parts[0].strip() if parts else ""
                value = "" if letter.upper() in ("N/A", "-", "") else letter

            results[course] = value

        return results

    async def logout(self) -> None:
        # nothing special to click—just let the session expire
        await self.page.wait_for_timeout(300)
