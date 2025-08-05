from __future__ import annotations
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple, List

from bs4 import BeautifulSoup  # type: ignore
from scraper.portals.base import PortalEngine  # type: ignore
from scraper.portals import register_portal  # type: ignore
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


@register_portal("infinite_campus_parent_gilbert")
class InfiniteCampus(PortalEngine):
    LOGIN = "https://gilbertaz.infinitecampus.org/campus/gilbert.jsp"
    HOME_WRAPPER = (
        "https://gilbertaz.infinitecampus.org/"
        "campus/nav-wrapper/parent/portal/parent/home?appName=gilbert"
    )
    LOGOFF = (
        "https://gilbertaz.infinitecampus.org/campus/"
        "portal/parents/gilbert.jsp?status=logoff"
    )

    # ---------------------- LOGIN (home only) ----------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Only log in and arrive on the parent/home shell."""
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
        await self.page.fill("input#username", self.sid)
        await self.page.fill("input#password", self.pw)
        await self.page.click("button:has-text('Log In')")
        await self.page.wait_for_url(lambda u: "parent/home" in u or "nav-wrapper" in u, timeout=15_000)
        await self.page.wait_for_load_state("networkidle")
        await self.page.wait_for_timeout(1500)  # small hard wait for Angular to attach
        print("[IC] Logged in and on parent/home.")

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
        if "parent/home" not in self.page.url:
            await self.page.goto(
                self.HOME_WRAPPER,
                wait_until="domcontentloaded",
            )
        await self.page.wait_for_timeout(1200)
        await self.page.wait_for_load_state("networkidle")

        html = await self.page.content()

        # Optional debug dump
        out_dir = Path(__file__).resolve().parents[2] / "output" / "debug"
        out_dir.mkdir(parents=True, exist_ok=True)
        dump = out_dir / f"home-notifications-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
        dump.write_text(html, encoding="utf-8")
        print(f"[IC] Wrote notifications HTML → {dump}")

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
