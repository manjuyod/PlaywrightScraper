"""
Updated Infinite Campus student portal scraper for Clark County School District (CCSD).

This module is based on ``scraper/portals/infinite_campus_student_ccsd.py`` from
the PlaywrightScraper repository.  The original implementation assumed
that grades were always delivered via an ``iframe#main-workspace``.
When this iframe is absent the original scraper would return an empty
result (sometimes showing ``about:blank``).  The updated version adds
a fallback that waits for grade cards rendered in the main document and
parses them accordingly.  Timeouts have been extended to improve
reliability on slower connections.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import List, Dict, Any, Optional, Tuple

from bs4 import BeautifulSoup  # type: ignore
from playwright.async_api import Page  # type: ignore
from .base import PortalEngine
from . import register_portal  # helper we'll create in __init__.py
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


@register_portal("infinite_campus_student_coral")
class InfiniteCampus(PortalEngine):
    """Student portal scraper for CCSD's Infinite Campus."""

    LOGIN = "https://nspcsa.infinitecampus.org/campus/portal/students/coral.jsp"
    GRADEBOOK = (
        "https://nspcsa.infinitecampus.org/campus/nav-wrapper/student/portal/student/grades?appName=coral"
    )
    LOGOFF = "https://nspcsa.infinitecampus.org/campus/portal/students/coral.jsp?status=logoff"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the student on the CCSD student portal.

        Args:
            first_name: Ignored for student portals.  Included to match
                the ``PortalEngine`` interface which allows callers to
                specify a student name when multiple profiles are
                available (not applicable for student logins).
        """
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
        await self.page.fill("input#username", self.sid)
        await self.page.fill("input#password", self.pw)
        # short debounce
        await self.page.wait_for_timeout(200)
        # submit the form via Enter key
        await self.page.locator('.form-group input[name="password"]').press("Enter")
        # wait for redirection to home page
        await self.page.wait_for_url(lambda url: "home" in url, timeout=15_000)
        await self.page.wait_for_load_state("networkidle")

    async def fetch_grades(self) -> Dict[str, Any]:
        """
        Stay on HOME and scrape notifications. Return latest Quarter Grade per subject
        filtered by first_name (like match). Payload shaped for DB insert.
        """
        # Ensure we're on home
        if "/home" not in self.page.url:
            await self.page.goto(self.HOME_WRAPPER, wait_until="domcontentloaded")

        await self.page.wait_for_timeout(1200)
        await self.page.wait_for_load_state("networkidle")

        html = await self.page.content()

        like_name = (getattr(self, "student_name", None) or "").strip()
        parsed_dict = self._parse_quarter_from_notifications(html, first_name=like_name)
        return {"parsed_grades": parsed_dict}

    # ---------------------- PARSER (Quarter) --------------------------------
    def _parse_quarter_from_notifications(
        self, html: str, first_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Parse 'Quarter Grade' updates from notifications on HOME.

        Accepts variants like:
          "... in Algebra 2: Quarter Grade"
          "... in Algebra 2: CASLV Quarter Grade"
          "... in Algebra 2: Q1 Quarter Grade"

        Returns: {"<SUBJECT>": <percent (float) or letter>}
        """
        soup = BeautifulSoup(html or "", "html.parser")

        # Do NOT depend on a specific UL wrapper; Coral markup may differ.
        items = soup.select("li.notification__container")
        if not items:
            # Fallback: look for any 'a.notification__text' links
            items = [n.parent.parent for n in soup.select("a.notification__text")]  # best-effort
            if not items:
                print("[IC Coral] No notification containers found.")
                return {}

        name_like = (first_name or "").strip().lower()

        # Regex: letter &/or percent, then "in SUBJECT: <anything> Quarter Grade"
        # - SUBJECT captured non-greedily up to the colon.
        pat = re.compile(
            r"has an updated grade of\s+"
            r"(?:(?P<letter>[A-F][+-]?)\s*)?"
            r"(?:\((?P<pct>\d{1,3}(?:\.\d+)?)%\))?\s+"
            r"in\s+(?P<subject>.+?)\s*:\s*(?:[A-Za-z0-9&/\- ]*\s+)?Quarter Grade\b",
            re.IGNORECASE,
        )

        def parse_notif_dt(txt: str) -> Optional[datetime]:
            s = txt.strip()
            now = datetime.now()
            # Today/Yesterday with optional time
            m_time = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM))", s, re.IGNORECASE)
            if s.lower().startswith("today"):
                t = datetime.strptime(m_time.group(1), "%I:%M %p").time() if m_time else datetime.strptime("12:00 PM", "%I:%M %p").time()
                return datetime.combine(now.date(), t)
            if s.lower().startswith("yesterday"):
                t = datetime.strptime(m_time.group(1), "%I:%M %p").time() if m_time else datetime.strptime("12:00 PM", "%I:%M %p").time()
                return datetime.combine((now - timedelta(days=1)).date(), t)
            # Weekday or plain date formats like "Fri, 8/1/25" or "8/1/25"
            for fmt in ("%a, %m/%d/%y", "%m/%d/%y"):
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    continue
            return None

        latest: Dict[str, Tuple[datetime, Any]] = {}

        for li in items:
            a = li.select_one("a.notification__text")
            d = li.select_one("p.notification__date")
            if not a or not d:
                # Some containers might be partial; skip quietly
                continue

            text = " ".join(a.get_text(" ", strip=True).split())
            date_str = d.get_text(" ", strip=True)

            # Like-filter by first name if provided (e.g., "kian" matches "Kian")
            if name_like and name_like not in text.lower():
                continue

            m = pat.search(text)
            if not m:
                continue

            dt = parse_notif_dt(date_str) or datetime.min
            subject = m.group("subject").strip()
            letter = (m.group("letter") or "").strip() or None
            pct = m.group("pct")

            # Prefer % if present, else letter
            if pct is not None:
                try:
                    value: Any = float(pct)
                except ValueError:
                    value = pct  # raw fallback
            elif letter:
                value = letter
            else:
                continue

            if subject not in latest or dt > latest[subject][0]:
                latest[subject] = (dt, value)

        result = {subj: val for subj, (dt, val) in latest.items()}
        print(f"[IC Coral] Parsed {len(result)} quarter-grade notifications (filtered by {first_name!r}).")
        if result:
            print("[IC Coral] Sample:", list(result.items())[:3])
        return result
