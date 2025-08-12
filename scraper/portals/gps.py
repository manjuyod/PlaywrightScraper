from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple
import re
from datetime import datetime, timedelta  # ← added timedelta
from bs4 import BeautifulSoup  # type: ignore
# from playwright.async_api import Page  # not used
from .base import PortalEngine
from . import register_portal  # ← removed nonexistent import infinite_campus_parent_gilbert
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from time import sleep

# TODO: Uses Infinite Campus after RapidIdentity; you can remove those pieces later if not needed.
# TODO (in progress): Implement 2FA Handling (Select pictures)

@register_portal("gps")
class GPS(PortalEngine):
    """Portal scraper for Gilbert Public Schools' portal.

    The class uses Playwright to automate login and extract quarter grades
    for each course. Grades are returned as a list of course/grade
    dictionaries under the ``parsed_grades`` key.
    """

    LOGIN = "https://gpsportal.gilberted.net/"
    HOME_WRAPPER = (
        "https://gilbertaz.infinitecampus.org/"
        "campus/nav-wrapper/parent/portal/parent/home?appName=gilbert"
    )
    LOGOFF = (
        "https://gilbertaz.infinitecampus.org/campus/"
        "portal/parents/gilbert.jsp?status=logoff"
    )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the user on the GPS parent portal."""
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")

        # Username
        await self.page.fill("input#identification", self.sid)
        await self.page.wait_for_timeout(200)
        await self.page.click("button#authn-go-button")

        # Password
        await self.page.fill("input#ember534", self.pw)
        await self.page.wait_for_timeout(2000)
        await self.page.locator("#authn-go-button").evaluate("btn => btn.click()")

        # RapidIdentity often doesn't change URL. Wait for pictograph tiles instead.
        await self.page.locator(".pictograph-list img.tile-icon").first.wait_for(
            state="visible", timeout=15_000
        )
        await self.page.wait_for_load_state("networkidle")
        print("trying pictograph\n")

        # Pictograph auth (three picks)
        for _ in range(0, 3):
            images_alts = await self.page.eval_on_selector_all(
                ".pictograph-list img.tile-icon", "imgs => imgs.map(img => img.alt)"
            )
            assert self.auth_images is not None  # must be provided by caller/DB
            user_match = next((image for image in self.auth_images if image in images_alts), None)
            if not user_match:
                raise RuntimeError(f"No pictograph match found in {images_alts} for {self.auth_images}")
            await self.page.locator(
                f".pictograph-list img.tile-icon[alt='{user_match}']"
            ).click()
            await self.page.wait_for_timeout(2000)

        # (Optional) Navigate to Infinite Campus tile if present
        try:
            await self.page.locator("img[alt='STUDENT INFINITE CAMPUS']").click()
            await self.page.wait_for_timeout(200)
            await self.page.wait_for_load_state("networkidle")
        except Exception:
            # If IC isn't part of your flow, it's safe to ignore.
            pass

    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """Stay on HOME and scrape notifications; return latest Semester Grade per subject."""
        if "/home" not in self.page.url:
            await self.page.goto(self.HOME_WRAPPER, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(1200)
        await self.page.wait_for_load_state("networkidle")

        html = await self.page.content()
        like_name = (getattr(self, "student_name", None) or "").strip()
        parsed_dict = self._parse_semester_from_notifications(html, first_name=like_name)

        return {"parsed_grades": parsed_dict}

    # ---------------------- PARSER ------------------------------------------
    def _parse_semester_from_notifications(
        self, html: str, first_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Parse 'Semester Grade' updates from notifications on HOME.
        Example: "… has an updated grade of D (61.90%) in ALGEBRA II: Semester Grade"
        → {"ALGEBRA II": 61.90}
        """
        soup = BeautifulSoup(html or "", "html.parser")
        ul = soup.select_one("ul.notifications-dropdown__body")
        if not ul:
            print("[IC] No notifications list found.")
            return {}

        name_like = (first_name or "").strip().lower()

        pat = re.compile(
            r"has an updated grade of\s+"
            r"(?:(?P<letter>[A-F][+-]?)\s*)?"
            r"(?:\((?P<pct>\d{1,3}(?:\.\d+)?)%\))?\s+"
            r"in\s+(?P<subject>.+?):\s*Semester Grade",
            re.IGNORECASE,
        )

        def parse_notif_dt(txt: str) -> Optional[datetime]:
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

        for li in ul.select("li.notification__container"):
            a = li.select_one("a.notification__text")
            d = li.select_one("p.notification__date")
            if not a or not d:
                continue

            text = " ".join(a.get_text(" ", strip=True).split())
            date_str = d.get_text(" ", strip=True)

            if name_like and name_like not in text.lower():
                continue

            m = pat.search(text)
            if not m:
                continue

            dt = parse_notif_dt(date_str) or datetime.min
            subject = m.group("subject").strip()
            letter = (m.group("letter") or "").strip() or None
            pct = m.group("pct")

            if pct is not None:
                try:
                    value: Any = float(pct)
                except ValueError:
                    value = pct
            elif letter:
                value = letter
            else:
                continue

            if subject not in latest or dt > latest[subject][0]:
                latest[subject] = (dt, value)

        result = {subj: val for subj, (dt, val) in latest.items()}
        print(f"[IC] Parsed {len(result)} semester-grade notifications (filtered by {first_name!r}).")
        if result:
            print("[IC] Sample:", list(result.items())[:3])
        return result

    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
