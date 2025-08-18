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


@register_portal("infinite_campus_student_henderson")
class InfiniteCampusHenderson(PortalEngine):
    """Student portal scraper for Henderson's Infinite Campus via Microsoft SSO."""

    #: Login landing page (not used directly since we use SSO)
    LOGIN = (
        "https://nvcloud1.infinitecampus.org/campus/portal/students/henderson.jsp"
    )
    #: Direct SSO entry point for Henderson.  Using this URL avoids the
    #: intermediate warning that requires clicking "Sign In With Microsoft".
    SSO_URL = (
        "https://nvcloud1.infinitecampus.org/campus/SSO/henderson/portal/students?configID=10"
    )
    #: Home wrapper used once logged in to ensure we land on the home page.
    HOME_WRAPPER = (
        "https://nvcloud1.infinitecampus.org/campus/nav-wrapper/student/portal/home?appName=henderson"
    )
    #: Logoff URL to explicitly end the session (not used here but provided for completeness).
    LOGOFF = (
        "https://nvcloud1.infinitecampus.org/campus/portal/students/henderson.jsp?status=logoff"
    )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the student via Microsoft single sign‑on.

        Henderson's Infinite Campus portal requires SAML authentication
        through Microsoft.  This method navigates directly to the SSO
        link, enters the student's email (``sid``) and password
        (``pw``), and handles the optional "Stay signed in" prompt.

        Args:
            first_name: Ignored for student portals.  Included to match
                the ``PortalEngine`` interface which allows callers to
                specify a student name when multiple profiles are
                available (not applicable for student logins).
        """
        # Start tracing to capture screenshots for debugging (optional)
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        # Navigate directly to the SSO entry point
        await self.page.goto(self.SSO_URL, wait_until="domcontentloaded")
        # Enter email/username
        await self.page.fill("input[type='email']", self.sid)
        # Click the Next/Submit button for the email step; selectors may vary so
        # we target a generic submit button
        await self.page.locator("button[type='submit'], input[type='submit']").click()
        # Wait for the password field to appear
        await self.page.wait_for_selector("input[type='password']", timeout=15_000)
        # Enter password
        await self.page.fill("input[type='password']", self.pw)
        # Submit credentials
        await self.page.locator("button[type='submit'], input[type='submit']").click()
        # If Microsoft prompts "Stay signed in?", choose Yes to keep the session alive
        try:
            # Wait briefly for the prompt; if not present this will time out
            await self.page.wait_for_selector(
                "input[type='submit'], button#idSIButton9", timeout=5_000
            )
            # Click the Yes button (idSIButton9 is typically used by Microsoft)
            await self.page.locator(
                "button#idSIButton9, input[type='submit']"
            ).click()
        except Exception:
            pass  # no prompt shown
        # Wait until we're redirected to the home page
        await self.page.wait_for_url(lambda url: "/home" in url if url else False, timeout=30_000)
        await self.page.wait_for_load_state("networkidle")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """Scrape quarter grade notifications from the home page.

        The Infinite Campus home page displays a notification bell with
        grade updates.  We stay on the home page (``/home``) and parse
        the most recent "Quarter Grade" notifications, filtering by
        ``student_name`` if provided.  The result is shaped for
        database insertion: ``{"parsed_grades": {<Course>: <grade>, ...}}``.
        """
        # Ensure we are on the home page; if not, navigate there
        if "/home" not in self.page.url:
            await self.page.goto(self.HOME_WRAPPER, wait_until="domcontentloaded")
        # Allow the page to settle
        await self.page.wait_for_timeout(1_200)
        await self.page.wait_for_load_state("networkidle")
        # Grab the full HTML for parsing
        html = await self.page.content()
        like_name = (getattr(self, "student_name", None) or "").strip()
        parsed_dict = self._parse_quarter_from_notifications(html, first_name=like_name)
        return {"parsed_grades": parsed_dict}

    # ------------------------------------------------------------------
    # Notification parser
    def _parse_quarter_from_notifications(
        self, html: str, first_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Extract "Quarter Grade" notifications from the notifications dropdown.

        Notifications are rendered inside a list of ``li.notification__container``
        elements.  Each notification contains a link with class
        ``notification__text`` and a time label ``notification__date``.
        The text typically looks like::

            "Kian has an updated grade of C (70.00%) in Algebra 2: CASLV Quarter Grade"

        The parser supports variants such as "…: Q1 Quarter Grade" and
        "…: Quarter Grade" without additional qualifiers.  It filters
        notifications by ``first_name`` if provided (case-insensitive
        substring match).  When both a percentage and a letter grade
        appear, the percentage is used; otherwise the letter grade is
        returned.  Only the most recent notification per subject is kept.
        """
        soup = BeautifulSoup(html or "", "html.parser")
        # Attempt to find all notification containers without depending on a specific UL
        items = soup.select("li.notification__container")
        if not items:
            # Fallback: find anchors and climb to their container if structure differs
            items = [n.parent.parent for n in soup.select("a.notification__text")] or []
            if not items:
                # No notifications found
                return {}
        name_like = (first_name or "").strip().lower()
        # Pattern: captures optional letter and percentage, then subject before the colon
        pat = re.compile(
            r"has an updated grade of\s+"
            r"(?:(?P<letter>[A-F][+-]?)\s*)?"
            r"(?:\((?P<pct>\d{1,3}(?:\.\d+)?)%\))?\s+"
            r"in\s+(?P<subject>.+?)\s*:\s*(?:[A-Za-z0-9&/\- ]*\s+)?Quarter Grade\b",
            re.IGNORECASE,
        )

        def parse_notif_dt(txt: str) -> Optional[datetime]:
            """Parse the notification timestamp into a ``datetime``.

            Supports formats like "Today, 4:52 PM", "Yesterday, 11:23 AM",
            "Fri, 8/1/25" and "8/1/25".  Returns ``None`` if parsing fails.
            """
            s = txt.strip()
            now = datetime.now()
            m_time = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM))", s, re.IGNORECASE)
            if s.lower().startswith("today"):
                t = (
                    datetime.strptime(m_time.group(1), "%I:%M %p").time()
                    if m_time
                    else datetime.strptime("12:00 PM", "%I:%M %p").time()
                )
                return datetime.combine(now.date(), t)
            if s.lower().startswith("yesterday"):
                t = (
                    datetime.strptime(m_time.group(1), "%I:%M %p").time()
                    if m_time
                    else datetime.strptime("12:00 PM", "%I:%M %p").time()
                )
                return datetime.combine((now - timedelta(days=1)).date(), t)
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
                continue
            text = " ".join(a.get_text(" ", strip=True).split())
            date_str = d.get_text(" ", strip=True)
            # Filter by first name if provided
            if name_like and name_like not in text.lower():
                continue
            m = pat.search(text)
            if not m:
                continue
            dt = parse_notif_dt(date_str) or datetime.min
            subject = m.group("subject").strip()
            letter = (m.group("letter") or "").strip() or None
            pct = m.group("pct")  # string or None
            if pct is not None:
                try:
                    value: Any = float(pct)
                except ValueError:
                    value = pct
            elif letter:
                value = letter
            else:
                continue
            # Keep the most recent notification per subject
            if subject not in latest or dt > latest[subject][0]:
                latest[subject] = (dt, value)
        return {subj: val for subj, (dt, val) in latest.items()}
