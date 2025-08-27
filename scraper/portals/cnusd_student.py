"""
Playwright scraper for the Corona‑Norco USD (CNUSD) StudentConnection portal.

This engine automates login to the StudentConnection portal using a
student PIN and password, then navigates to the Pulse dashboard to
extract current percentage grades for each class.  Results are returned
as a dictionary under the key ``parsed_grades`` as expected by the
scraper framework.

The implementation reuses the parsing logic from ``cnusd_parser.py`` but
integrates it directly here so that no external dependencies are
required.  It identifies the Pulse table by its id (``SP-Pulse``) and
extracts the "Class", "Term", and "Pct" columns.  If a numeric
percentage is unavailable for a given course, the scraper will fall
back to the letter grade if present and otherwise skip the course.

Usage: register this portal with the key ``cnusd`` and call
``login()`` followed by ``fetch_grades()`` to obtain grades.
"""

from __future__ import annotations

import email
from email.message import Message
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from bs4 import BeautifulSoup  # type: ignore
from playwright.async_api import Page, TimeoutError  # type: ignore
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import PortalEngine
from . import register_portal


@register_portal("cnusd_student")
class CNUSD(PortalEngine):
    """Scraper for the CNUSD StudentConnection portal.

    This engine logs into the portal using the provided PIN (as
    ``student_id``) and password, then extracts grades from the Pulse
    widget on the home page.  Grades are returned as a dictionary
    mapping class names to their percentage or letter grade under the
    ``parsed_grades`` key.
    """

    LOGIN = "https://studentconnect.cnusd.k12.ca.us/"
    HOME_WRAPPER = "https://studentconnect.cnusd.k12.ca.us/Home/PortalMainPage"
    LOGOFF = "https://studentconnect.cnusd.k12.ca.us/Home/Logout"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=3, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the user on the CNUSD StudentConnection portal.

        Args:
            first_name: Optional first name to filter on when parsing
                grades.  The CNUSD portal does not have a student
                selector like some parent portals, so this argument is
                ignored during login but accepted for API compatibility.
        """
        # Start tracing for debugging and audit (screenshots and DOM snapshots)
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        # Navigate to login page
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
        # Fill in the PIN and password fields.  The login form uses
        # input names "Pin" and "Password".  A hidden "districtid"
        # field is automatically submitted with the form action.
        await self.page.fill("input[name='Pin']", self.sid)
        await self.page.fill("input[name='Password']", self.pw)
        # Wait briefly to ensure values are registered
        await self.page.wait_for_timeout(500)
        # Click the Login button.  It is the only button within the form.
        # Use a locator that searches for a button containing the text 'Login'.
        login_button = self.page.locator("form button:has-text('Login')")
        if await login_button.is_visible():
            await login_button.click()
        else:
            # Fallback: press Enter in the password field
            await self.page.locator("input[name='Password']").press("Enter")
        # Wait until the URL contains 'PortalMainPage' indicating successful login
        await self.page.wait_for_url(lambda url: "PortalMainPage" in url, timeout=20_000)
        # Wait for network to be idle to ensure the home page has loaded
        await self.page.wait_for_load_state("networkidle")
        # Stop tracing after login
        await self.page.context.tracing.stop()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=3, max=10),
        retry=retry_if_exception_type(Exception),
    )
    
    async def fetch_grades(self) -> Dict[str, Any]:
        """
        Scrape the Pulse table on PortalMainPage and return:
        {"parsed_grades": {"COURSE NAME": 93.4 or "A", ...}}
        """
        # Ensure we’re on the main page
        if "PortalMainPage" not in self.page.url:
            await self.page.goto(
                "https://studentconnect.cnusd.k12.ca.us/Home/PortalMainPage",
                wait_until="domcontentloaded"
            )

        # Brief pause to allow initial widgets to paint
        await self.page.wait_for_timeout(800)

        # Try to ensure Pulse section is visible/expanded
        try:
            img_pulse = self.page.locator("#img_Pulse")
            if await img_pulse.count() > 0:
                expanded = await img_pulse.get_attribute("aria-expanded")
                # Some builds use 'true'/'false', others omit; click if clearly collapsed
                if expanded is not None and expanded.lower() in ("false", "collapsed"):
                    await img_pulse.click()
                    await self.page.wait_for_timeout(400)
        except Exception:
            pass  # Not fatal—continue and rely on table presence

        # Wait for the Pulse table to exist in the DOM. If not, click left-menu "Pulse".
        try:
            await self.page.locator("#SP-Pulse").wait_for(state="attached", timeout=10_000)
        except TimeoutError:
            try:
                menu_pulse = self.page.locator("tr#Pulse, td.td2_action:has-text('Pulse')")
                if await menu_pulse.count() > 0:
                    await menu_pulse.first.click()
                    await self.page.wait_for_timeout(500)
                    await self.page.locator("#SP-Pulse").wait_for(state="attached", timeout=7_000)
            except Exception:
                pass

        # Wait until tbody has at least one row with cells (guards against hydration lag)
        try:
            await self.page.wait_for_function(
                """(sel) => {
                    const t = document.querySelector(sel);
                    if (!t) return false;
                    const body = t.tBodies && t.tBodies[0];
                    if (!body || !body.rows || body.rows.length === 0) return false;
                    return body.rows[0].cells && body.rows[0].cells.length > 0;
                }""",
                arg="#SP-Pulse",
                timeout=8_000,
            )
        except TimeoutError:
            html = await self.page.content()
            print("[CNUSD] Pulse table had no rows. Dumping snippet for debug…")
            print(html[:4000])
            return {"parsed_grades": {}}

        # Map the header indices so we don’t rely on column order.
        header_cells = self.page.locator("#SP-Pulse thead th")
        header_count = await header_cells.count()
        header_texts: list[str] = []
        for i in range(header_count):
            try:
                t = await header_cells.nth(i).text_content()
                header_texts.append((t or "").strip())
            except Exception:
                header_texts.append("")

        def col_idx(name: str) -> int | None:
            lname = name.lower()
            for i, h in enumerate(header_texts):
                if h.lower() == lname:
                    return i
            return None

        idx_class  = col_idx("Class")
        idx_term   = col_idx("Term")
        idx_pct    = col_idx("Pct")
        idx_letter = col_idx("CurrentGrade")

        if idx_class is None or (idx_pct is None and idx_letter is None):
            html = await self.page.content()
            print("[CNUSD] Missing expected headers. Headers seen:", header_texts)
            print(html[:4000])
            return {"parsed_grades": {}}

        # Extract rows
        rows = self.page.locator("#SP-Pulse tbody tr")
        n = await rows.count()
        parsed: Dict[str, Any] = {}

        for r in range(n):
            cells = rows.nth(r).locator("td")
            ccount = await cells.count()
            if ccount == 0:
                continue

            async def safe_text(i: int | None) -> str:
                if i is None or i < 0 or i >= ccount:
                    return ""
                try:
                    t = await cells.nth(i).text_content()
                    return (t or "").strip()
                except Exception:
                    return ""

            course = (await safe_text(idx_class)).upper()
            term   = await safe_text(idx_term) if idx_term is not None else ""
            pct_s  = await safe_text(idx_pct)  if idx_pct  is not None else ""
            letter = await safe_text(idx_letter) if idx_letter is not None else ""

            # Normalize percentage: "82.0%" → 82.0
            value: Any
            if pct_s:
                pct_norm = pct_s.replace("%", "").replace("(", "").replace(")", "").strip()
                try:
                    value = float(pct_norm)
                except ValueError:
                    value = pct_s  # unexpected formatting, keep raw
            elif letter:
                value = letter
            else:
                continue

            if course:
                parsed[course] = value

        if not parsed:
            html = await self.page.content()
            print("[CNUSD] Parsed 0 rows. First 4k of HTML follows:")
            print(html[:4000])

        return {"parsed_grades": parsed}
