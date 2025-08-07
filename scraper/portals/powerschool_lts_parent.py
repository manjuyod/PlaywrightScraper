from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup
import re

from scraper.portals.base import PortalEngine
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


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
            # 1) Course cell has class-element text
            course_td = tr.select_one("td.table-element-text-align-start")
            if not course_td:
                continue
            
            # raw like "SC 08 - Science  Email Costa, Tai – Rm: 231"
            course_raw = course_td.get_text(" ", strip=True)
            # remove any "Email ... – Rm: ..." or similar suffix
            course = re.sub(r"\sEmail\s.*?$", "", course_raw)
            # also strip off any numeric code prefix (e.g. "SC 08 - ")
            # strip any prefix code "SC 08 - "
            course = re.sub(r"^[A-Z0-9 ]+-\s*", "", course_raw)

            # 2) Find all <a class="bold">…</a> – term links
            bold_links = tr.select("a.bold")
            if not bold_links:
                # maybe N/A or missing → record empty
                results[course] = ""
                continue

            # take the last one (usually S1 / current term)
            link = bold_links[-1]
            text = link.get_text("\n", strip=True)  # e.g. "B\n87"
            parts = text.splitlines()
            # look for percentage numeric part
            value: Any = ""
            if len(parts) >= 2 and parts[1].isdigit():
                # parts[1] is e.g. '87'
                try:
                    value = float(parts[1])
                except ValueError:
                    value = parts[1]
            else:
                # fallback to letter or raw
                letter = parts[0].strip()
                value = "" if letter.upper() in ("N/A", "-", "") else letter

            results[course] = value

        return results

    async def logout(self) -> None:
        # nothing special to click—just let the session expire
        await self.page.wait_for_timeout(300)
