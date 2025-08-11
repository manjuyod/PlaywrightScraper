from __future__ import annotations

from typing import List, Dict, Any, Optional

from bs4 import BeautifulSoup  # type: ignore
from playwright.async_api import Page  # type: ignore
from .base import PortalEngine
from . import register_portal, infinite_campus_parent_gilbert  # helper we'll create in __init__.py
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from time import sleep
#TODO: USES INFINITE CAMPUS GILBERT AS THEIR PORTAL CAN WE SKIP THIS STEP
#TODO in progress: Implement 2FA Handling (Select pictures)

@register_portal("gpsportal")
class GPS(PortalEngine):
    """Portal scraper for Gilbert Public Schools' portal.

    The class uses Playwright to automate login and extract quarter grades
    for each course.  Grades are returned as a list of course/grade
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
        reraise=True
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        # print("HELLO LOGGING IN DO MY PRINT STATEMENTS WORK AT ALL\n\n\n\n\n\n")
        """Authenticate the user on the GPS parent portal.

        Args:
            first_name: An optional first name for selecting a specific
                student profile after login.  The GPS parent portal
                currently does not expose multiple profiles per login,
                so this argument is ignored, but it is accepted for
                compatibility with the ``PortalEngine`` interface.
        """
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
        # Fill username
        await self.page.fill("input#identification", self.sid)
        # Short pause to ensure field is recognized
        await self.page.wait_for_timeout(200)
        # Click Go
        await self.page.click("button#authn-go-button")
        # Fill Password
        await self.page.fill("input#ember534", self.pw)
        await self.page.wait_for_timeout(2000)
        await self.page.locator("#authn-go-button").evaluate("btn => btn.click()")
        await self.page.wait_for_url(lambda url: "pictograph" in url, timeout=15_000)
        await self.page.wait_for_load_state("networkidle")
        print("trying pictograph\n")
        # Must authenticate for this page using the GPSPortalImage field contained in the database
        # Collect the image options on the screen
        for _ in range(0, 3): # three images to select
            images_alts = await self.page.eval_on_selector_all(
                ".pictograph-list img.tile-icon",
                "imgs => imgs.map(img => img.alt)"
            )
            assert self.auth_images is not None # auth images should never be null for this portal
            user_match = next((image for image in self.auth_images if image in images_alts), None)
            await self.page.locator(f".pictograph-list img.tile-icon[alt='{user_match}']").click()
            await self.page.wait_for_timeout(2000)
        # finally nav to the gilbert infinite campus portal
        await self.page.locator("img[alt='STUDENT INFINITE CAMPUS']").click()
        await self.page.wait_for_timeout(200)
        await self.page.wait_for_load_state('networkidle')

    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """
        Stay on HOME and scrape notifications. Return latest Semester Grade per subject
        filtered by first_name (like match). Payload shaped for DB insert.
        """
        # Ensure we're on home
        if "/home" not in self.page.url:
            await self.page.goto(
                self.HOME_WRAPPER,
                wait_until="domcontentloaded",
            )
        await self.page.wait_for_timeout(1200)
        await self.page.wait_for_load_state("networkidle")

        html = await self.page.content()

        # Optional debug dump
        #out_dir = Path(__file__).resolve().parents[2] / "output" / "debug"
        #out_dir.mkdir(parents=True, exist_ok=True)
        #dump = out_dir / f"home-notifications-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
        #dump.write_text(html, encoding="utf-8")
        #print(f"[IC] Wrote notifications HTML → {dump}")

        like_name = (getattr(self, "student_name", None) or "").strip()
        parsed_dict = self._parse_semester_from_notifications(html, first_name=like_name)
        # ^ parsed_dict is already {"Course": 93.4 or "A", ...}

        # Shape exactly as requested
        return {
            "parsed_grades": parsed_dict
        }

    # ---------------------- PARSER ------------------------------------------
    def _parse_semester_from_notifications(self, html: str, first_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse 'Semester Grade' updates from notifications on HOME.

        Example text:
          "Theodor has an updated grade of D (61.90%) in ALGEBRA II: Semester Grade"
        Becomes:
          {"ALGEBRA II": 61.90}

        Rules:
          - Filter by first_name (substring/like, case-insensitive) if provided.
          - Prefer percentage (float, no %) over letter.
          - Keep the LATEST notification per subject based on the date label.
            Handles "Today, 9:14 AM", "Yesterday, 11:45 AM", and "Fri, 8/1/25".
        """
        soup = BeautifulSoup(html or "", "html.parser")
        ul = soup.select_one("ul.notifications-dropdown__body")
        if not ul:
            print("[IC] No notifications list found.")
            return {}

        name_like = (first_name or "").strip().lower()

        # Regex for: has an updated grade of <letter?> (<pct?>) in SUBJECT: Semester Grade
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
            # Today/Yesterday with optional time
            m_time = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM))", s, re.IGNORECASE)
            if s.lower().startswith("today"):
                t = datetime.strptime(m_time.group(1), "%I:%M %p").time() if m_time else datetime.strptime("12:00 PM", "%I:%M %p").time()
                return datetime.combine(now.date(), t)
            if s.lower().startswith("yesterday"):
                t = datetime.strptime(m_time.group(1), "%I:%M %p").time() if m_time else datetime.strptime("12:00 PM", "%I:%M %p").time()
                return datetime.combine((now - timedelta(days=1)).date(), t)
            # Weekday formats like "Fri, 8/1/25" or just "8/1/25"
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

            # Like-filter by first name if provided (e.g., "theo" matches "Theodor")
            if name_like and name_like not in text.lower():
                continue

            m = pat.search(text)
            if not m:
                continue

            dt = parse_notif_dt(date_str) or datetime.min
            subject = m.group("subject").strip()
            letter = (m.group("letter") or "").strip() or None
            pct = m.group("pct")  # string or None

            # Prefer % if present, else letter
            if pct is not None:
                try:
                    value: Any = float(pct)
                except ValueError:
                    value = pct  # raw string fallback (unlikely)
            elif letter:
                value = letter
            else:
                continue

            # Keep the latest per subject
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
