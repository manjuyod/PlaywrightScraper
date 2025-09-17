"""
Portal engine for the Tustin Unified School District Aeries Parent/Student portal.

This module implements the :class:`PortalEngine` interface for aeries portals hosted
by the Tustin USD.  The login flow follows the two‑step Aeries login form where
the user first enters their email/username and then supplies a password.  Once
authenticated the dashboard page exposes a JavaScript variable called
``RecentData`` which contains summary information for the currently logged in
student.  Each entry in this array describes a piece of portal content such as
classes, upcoming assignments, or recently viewed items.  For class entries the
``Details`` field encodes the grade letter and percentage as ``^``‑separated
values.  The scraper extracts these values to build a course/grade mapping.

The implementation borrows heavily from the existing Aeries LosAl scraper
provided by the user but has been tailored for the Tustin portal.  It avoids
hard‑coding element selectors where possible and includes basic fallbacks
should the DOM change.  If the login credentials are invalid a ``LoginError``
will be raised to signal failure.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup  # type: ignore
from tenacity import (retry, retry_if_exception_type, retry_if_not_exception_type,
                      stop_after_attempt, wait_exponential)

from .base import PortalEngine
from . import register_portal


class LoginError(Exception):
    """Raised when the scraper is unable to authenticate with provided credentials."""


@register_portal("aeries_tustin")
class AeriesTustin(PortalEngine):
    """Portal scraper for the Tustin USD Aeries portal.

    This engine logs into the portal and extracts the current semester grade
    for each enrolled class.  Grades are returned as a mapping of course
    names (upper‑cased) to either a numeric percentage (e.g. 92.0) or the
    letter grade when no percentage is provided.
    """

    # URLs used by the engine.  If these change in the future the class can
    # simply override the constants.
    LOGIN = "https://parentnet.tustin.k12.ca.us/ParentPortal/LoginParent.aspx"
    HOME_WRAPPER = "https://parentnet.tustin.k12.ca.us/ParentPortal/Dashboard.aspx"
    LOGOFF = "https://parentnet.tustin.k12.ca.us/ParentPortal/LogOff.aspx"

    # -------------------------------------------------------------------------
    # LOGIN
    # -------------------------------------------------------------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_not_exception_type(LoginError),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the user on the TUSD Aeries portal.

        The Aeries login process is two‑phased: users first enter their email
        address/username and click the next button, after which the password
        field becomes visible.  This method waits for each element to appear
        before interacting with it to accommodate slower network responses.
        """
        # Navigate to the login page
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")

        # Enter username/email
        # Many Aeries portals use an id of portalAccountUsername for the first
        # login box.  We attempt to use this first, falling back to the first
        # visible text input if necessary.
        try:
            await self.page.fill("input#portalAccountUsername", self.sid)
        except Exception:
            # fallback: find the first visible input field of type text or email
            await self.page.locator("input[type='text'], input[type='email']").first.fill(self.sid)

        # Click the next button.  Some portals use a button with id 'next' and
        # others use a generic button labelled 'Next'.  We try both.
        try:
            await self.page.locator("#next").click()
        except Exception:
            # fallback: button containing the text 'Next'
            await self.page.locator("button:has-text('Next'), input[type='submit']:has-text('Next')").first.click()

        # Wait for password field to appear
        await self.page.locator("input[type='password']").wait_for(state="visible", timeout=15_000)

        # Enter password
        try:
            await self.page.fill("input#portalAccountPassword", self.pw)
        except Exception:
            await self.page.locator("input[type='password']").first.fill(self.pw)

        # Click the login/sign in button.  Many Aeries forms use id 'LoginButton'.
        try:
            await self.page.locator("#LoginButton").click()
        except Exception:
            # fallback: button containing 'Sign In'
            await self.page.locator("button:has-text('Sign In'), input[type='submit']:has-text('Sign In')").first.click()

        # After submitting credentials wait for navigation to dashboard
        try:
            await self.page.wait_for_url(lambda url: "Dashboard" in url, timeout=45_000)
        except Exception:
            # Check for error message on the login form
            error_box = self.page.locator("#errorContainer, .alert-danger")
            if await error_box.is_visible():
                msg = await error_box.inner_text()
                raise LoginError(msg.strip())
            # If no explicit error just rethrow
            raise

    # -------------------------------------------------------------------------
    # FETCH
    # -------------------------------------------------------------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """Scrape current semester grades from the dashboard.

        Once logged in the dashboard page defines a JavaScript variable
        ``RecentData`` that contains an array of dictionaries.  For entries where
        ``Section`` is ``"Classes"`` the ``Details`` field encodes several pieces
        of information separated by caret characters (``^``).  The 5th element
        (index 4) is the letter grade and the 6th (index 5) is the percentage.
        Either value may be missing.  This method returns a dictionary mapping
        upper‑cased course names to a percentage (float) when provided or the
        letter grade when only a letter is available.
        """
        # Ensure we are on the dashboard.  If not, navigate there.
        if "Dashboard" not in self.page.url:
            await self.page.goto(self.HOME_WRAPPER, wait_until="domcontentloaded")

        # Small wait to allow any asynchronous content to initialise
        await self.page.wait_for_timeout(2_000)

        # Grab the full HTML of the page
        html = await self.page.content()

        # Extract the RecentData array from a script tag using regex.  The
        # structure looks like: ``var RecentData = [...]``.  We compile with
        # DOTALL to allow newlines within the array.
        m = re.search(r"var\s+RecentData\s*=\s*(\[.*?\]);", html, flags=re.S)
        courses: Dict[str, Any] = {}
        if m:
            arr_text = m.group(1)
            try:
                data = json.loads(arr_text)
            except json.JSONDecodeError:
                data = []
            # Filter class entries
            for item in data:
                try:
                    if item.get("Section") != "Classes":
                        continue
                    title = item.get("Title", "").strip()
                    details = item.get("Details", "")
                    parts = details.split("^")
                    # parts[4]: letter grade, parts[5]: percentage with '%'
                    letter = parts[4].strip() if len(parts) > 4 and parts[4] else None
                    pct_str = parts[5].strip().rstrip('%') if len(parts) > 5 and parts[5] else None
                    value: Any = None
                    if pct_str:
                        try:
                            value = float(pct_str)
                        except ValueError:
                            value = pct_str
                    elif letter:
                        value = letter
                    if title and value is not None:
                        courses[title.upper()] = value
                except Exception:
                    continue
        else:
            # As a fallback, attempt to parse the class cards similar to the LosAl
            # implementation.  This may be needed if the RecentData script
            # changes.  Parse the DOM using BeautifulSoup and look for grade
            # containers.
            soup = BeautifulSoup(html, "html.parser")
            cards = soup.find_all("div", class_=re.compile(r"Card"))
            for card in cards:
                # class name
                link = card.find(['a', 'span'], class_=re.compile(r"TextHeading|Heading", re.I))
                name = link.get_text(strip=True) if link else None
                # grade letter/percentage
                grade_div = card.find("div", class_=re.compile(r"Grade", re.I))
                grade_text = grade_div.get_text(" ", strip=True) if grade_div else None
                letter = None
                pct = None
                if grade_text:
                    # e.g. "A- (92.0%)" or "B (85.7%)"
                    m2 = re.match(r"([A-F][+-]?)\s*\((\d+(?:\.\d+)?)%\)", grade_text)
                    if m2:
                        letter, pct_str2 = m2.group(1), m2.group(2)
                        try:
                            pct = float(pct_str2)
                        except ValueError:
                            pct = pct_str2
                    else:
                        # maybe just letter or percent
                        m3 = re.match(r"([A-F][+-]?)", grade_text)
                        if m3:
                            letter = m3.group(1)
                if name and (pct is not None or letter is not None):
                    courses[name.upper()] = pct if pct is not None else letter

        return {"parsed_grades": courses}

    # -------------------------------------------------------------------------
    # LOGOUT
    # -------------------------------------------------------------------------
    async def logout(self) -> None:
        """Log out of the portal by navigating to the logoff URL."""
        if self.LOGOFF:
            await self.page.goto(self.LOGOFF)
            await self.page.wait_for_timeout(500)