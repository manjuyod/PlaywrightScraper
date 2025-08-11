from __future__ import annotations
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple, List

from bs4 import BeautifulSoup  # type: ignore
from scraper.portals.base import PortalEngine  # type: ignore
from scraper.portals import register_portal  # type: ignore
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


@register_portal("infinite_campus_parent_alac")
class InfiniteCampus(PortalEngine):
    LOGIN = "https://alaaz.infinitecampus.org/campus/portal/parents/ala.jsp"
    HOME_WRAPPER = "https://alaaz.infinitecampus.org/campus/nav-wrapper/parent/portal/parent/home?appName=ala"
    LOGOFF = "https://alaaz.infinitecampus.org/campus/portal/parents/ala.jsp?status=logoff"
    GRADES_URL = "https://alaaz.infinitecampus.org/campus/nav-wrapper/parent/portal/parent/grades?appName=ala"

    # Where to dump snapshots
    OUT_DIR = Path(__file__).resolve().parents[2] / "output" / "pages"

    # ---------------------- LOGIN (home only) ----------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Only log in and arrive on the parent/home shell."""
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
        await self.page.fill("input[name='username']", self.sid)
        await self.page.fill("input[name='pw'], input[name='password']", self.pw)
        await self.page.evaluate("() => document.querySelector('form#login').submit()")
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
        Try notifications first. If empty, navigate to Grades, dump MHTML + all frames to disk,
        parse the saved frame HTML files, and (optionally) clean them up.
        """
        # Ensure we're on home
        if "parent/home" not in self.page.url:
            await self.page.goto(self.HOME_WRAPPER, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(1200)
        await self.page.wait_for_load_state("networkidle")

        # Open notifications bell (best-effort) so lazy content renders
        try:
            await self.page.click('button[aria-label="Notifications"]', timeout=3_000)
            await self.page.wait_for_selector('ul.notifications-dropdown__body li.notification__container', timeout=3_000)
            await self.page.wait_for_timeout(200)
        except Exception:
            pass

        html = await self.page.content()
        like_name = (getattr(self, "student_name", None) or "").strip()

        parsed_dict = self._parse_semester_from_notifications(html, first_name=like_name)
        if parsed_dict:
            return {"parsed_grades": parsed_dict}

        # ---- SCORCHED EARTH: go to grades, dump everything, parse from disk ----
        try:
            parsed_dict = await self._grades_scorched_earth_parse_from_files(first_name=like_name)
        except Exception as e:
            print(f"[IC] Scorched-earth fallback failed: {e!r}")
            parsed_dict = {}

        return {"parsed_grades": parsed_dict}

    # ---------------------- NOTIFICATIONS PARSER ------------------------------
    def _parse_semester_from_notifications(self, html: str, first_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Accept both 'has an updated grade of' and 'received a new grade of', but REQUIRE 'Semester Grade'.
        """
        soup = BeautifulSoup(html or "", "html.parser")
        ul = soup.select_one("ul.notifications-dropdown__body")
        if not ul:
            print("[IC] No notifications list found.")
            return {}

        name_like = (first_name or "").strip().lower()

        pat = re.compile(
            r"(?:has an updated grade of|received a new grade of)\s+"
            r"(?:(?P<letter>[A-F][+-]?)\s*)?"
            r"(?:\((?P<pct>\d{1,3}(?:\.\d+)?)%\))?\s+"
            r"in\s+(?P<subject>.+?):\s*Semester\s+Grade",
            re.IGNORECASE,
        )

        def parse_notif_dt(txt: str) -> Optional[datetime]:
            s = txt.strip()
            now = datetime.now()
            m_time = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM))", s, re.IGNORECASE)
            if s.lower().startswith("today"):
                t = datetime.strptime(m_time.group(1), "%I:%M %p").time() if m_time else datetime.strptime("12:00 PM", "%I:%M %p").time()
                return datetime.combine(now.date(), t)
            if s.lower().startswith("yesterday"):
                t = datetime.strptime(m_time.group(1), "%I:%M %p").time() if m_time else datetime.strptime("12:00 PM", "%I:%M %p").time()
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
        print(f"[IC] Parsed {len(result)} 'Semester Grade' notifications (filtered by {first_name!r}).")
        if result:
            print("[IC] Sample:", list(result.items())[:3])
        return result

    # ---------------------- SCORCHED-EARTH FALLBACK --------------------------
    async def _grades_scorched_earth_parse_from_files(self, first_name: Optional[str]) -> Dict[str, Any]:
        """
        1) Navigate to Grades
        2) Save MHTML + MAIN + every FRAME to disk
        3) Parse *saved* FRAME HTML files for grades
        4) (Optional) delete files afterwards (commented-out by default)
        """
        # 1) Go to Grades
        if "parent/grades" not in self.page.url:
            await self.page.goto(self.GRADES_URL, wait_until="domcontentloaded")
        await self.page.wait_for_load_state("networkidle")
        await self.page.wait_for_timeout(800)

        # Try to pick student if switcher exists
        if first_name:
            try:
                btn = self.page.locator('[aria-label*="Student"], [data-cy*="student"], button:has-text("Student")')
                if await btn.first.is_visible():
                    await btn.first.click()
                    await self.page.wait_for_timeout(200)
                opt = self.page.locator(f'text=/^{re.escape(first_name)}/i')
                if await opt.first.is_visible():
                    await opt.first.click()
                    await self.page.wait_for_timeout(400)
            except Exception:
                pass

        # 2) Save everything to disk with a unique session prefix
        session_prefix = f"IC_ALAC_GRADES-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        try:
            await self._save_mhtml(self.page, self.OUT_DIR, prefix=session_prefix)
        except Exception as e:
            print(f"[IC] MHTML snapshot failed (non-fatal): {e!r}")

        main_path, frame_paths = await self._save_dom_htmls(self.page, self.OUT_DIR, prefix=session_prefix)

        print(f"[IC] Saved {1 + len(frame_paths)} HTML files to {self.OUT_DIR} (session={session_prefix}).")
        for i, fp in enumerate(frame_paths):
            print(f"  - Frame file #{i}: {fp}")

        # 3) Parse saved FRAME HTMLs (card-first, then table, then label-based)
        combined: Dict[str, Any] = {}
        for idx, fp in enumerate(frame_paths):
            try:
                html = Path(fp).read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                print(f"[IC] Could not read frame file {fp}: {e!r}")
                continue

            part = (
                self._parse_semester_from_collapsible_cards(html)
                or self._parse_semester_from_grades_table(html)
                or self._parse_semester_from_grades_view(html)
            )

            if part:
                print(f"[IC] Parsed {len(part)} subjects from disk frame #{idx}.")
                combined.update(part)

        print(f"[IC] Disk-parse combined subjects: {len(combined)}")
        if combined:
            print("[IC] Sample:", list(combined.items())[:3])

        # 4) Optional cleanup (DISABLED by default — uncomment to enable)
        try:
            Path(main_path).unlink(missing_ok=True)
            for fp in frame_paths:
                Path(fp).unlink(missing_ok=True)
            print(f"[IC] Cleaned up saved HTML files for session={session_prefix}.")
        except Exception as e:
            print(f"[IC] Cleanup failed (non-fatal): {e!r}")

        return combined

    # ---------------------- GRADES TABLE/GRID PARSER -------------------------
    def _parse_semester_from_collapsible_cards(self, html: str) -> Dict[str, Any]:
        """
        Parse Grades from 'collapsible-card grades__card' layout:
        <h4>English 7 - A</h4>  <-- subject (strip trailing ' - A', strip leading 'IN ')
        row: LEFT = 'Semester Grade', RIGHT = 'A' '(100%)' 'In-progress'
        Prefer percent if present, else letter, else raw text.
        Returns {SUBJECT: value} with SUBJECT uppercased for dedupe.
        """
        soup = BeautifulSoup(html or "", "html.parser")
        out: Dict[str, Any] = {}

        cards = soup.select("div.collapsible-card.grades__card")
        for card in cards:
            # Subject from header
            h = card.select_one(".collapsible-card__header h4")
            subject_raw = h.get_text(" ", strip=True) if h else ""
            if not subject_raw:
                continue
            # Normalize subject like: "IN English 7 - A" -> "English 7"
            subject = re.sub(r"^\s*IN\s+", "", subject_raw, flags=re.I)
            subject = re.sub(r"\s+-\s+[A-F][+-]?$", "", subject).strip()
            subject_norm = subject.upper()

            # Find the row whose LEFT says "Semester Grade"
            for row in card.select("div.grades__row"):
                left = row.select_one(".grades__flex-row__item--left")
                left_txt = left.get_text(" ", strip=True) if left else ""
                if not re.search(r"\bSemester\s+Grade\b", left_txt, flags=re.I):
                    continue

                right = row.select_one(".grades__flex-row__item--right")
                right_txt = right.get_text(" ", strip=True) if right else ""

                # Prefer % if present, else letter, else raw
                m_pct = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", right_txt)
                if m_pct:
                    try:
                        val: Any = float(m_pct.group(1))
                    except ValueError:
                        val = m_pct.group(1)
                else:
                    m_letter = re.search(r"\b([A-F][+-]?)\b", right_txt)
                    val = m_letter.group(1) if m_letter else right_txt or None

                if val is not None:
                    out[subject_norm] = val

        return out

    def _parse_semester_from_grades_table(self, html: str) -> Dict[str, Any]:
        """
        Parse Grades from table/grid markup by column headers.
        Looks for a course column and a grade column among:
          'Semester Grade', 'Final Grade', 'Grade'
        Returns {SUBJECT: value} where value is float% if parsable else letter/string.
        """
        soup = BeautifulSoup(html or "", "html.parser")

        def norm(txt: str) -> str:
            return re.sub(r"\s+", " ", (txt or "").strip())

        tables = soup.select("table") or soup.select('[role="table"]')
        out: Dict[str, Any] = {}

        for tbl in tables:
            headers = [norm(th.get_text(" ", strip=True)) for th in tbl.select("thead th")]
            if not headers:
                headers = [norm(el.get_text(" ", strip=True)) for el in tbl.select('[role="columnheader"]')]
            if not headers:
                continue

            course_idx = None
            grade_idx = None
            for i, h in enumerate(headers):
                h_low = h.lower()
                if course_idx is None and any(k in h_low for k in ["course", "subject", "class"]):
                    course_idx = i
                if grade_idx is None and any(k in h_low for k in ["semester grade", "final grade", "grade"]):
                    grade_idx = i

            if grade_idx is None or course_idx is None:
                continue

            rows = tbl.select("tbody tr") or tbl.select('[role="rowgroup"] [role="row"]')
            for r in rows:
                cells = r.select("td") or r.select('[role="gridcell"]')
                if len(cells) <= max(course_idx, grade_idx):
                    continue

                course = norm(cells[course_idx].get_text(" ", strip=True))
                grade_raw = norm(cells[grade_idx].get_text(" ", strip=True))
                if not course or not grade_raw:
                    continue

                # Normalize subject: drop leading "IN " and trailing " - X"
                course = re.sub(r"^\s*IN\s+", "", course, flags=re.I)
                course = re.sub(r"\s+-\s+[A-F][+-]?$", "", course)

                m_pct = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", grade_raw)
                m_letter = re.search(r"\b([A-F][+-]?)\b", grade_raw)
                if m_pct:
                    try:
                        val: Any = float(m_pct.group(1))
                    except ValueError:
                        val = m_pct.group(1)
                elif m_letter:
                    val = m_letter.group(1)
                else:
                    val = grade_raw

                out[course.upper()] = val

        return out

    # ---------------------- GRADES TEXT LABEL PARSER -------------------------
    def _parse_semester_from_grades_view(self, html: str) -> Dict[str, Any]:
        """
        Flexible parser for the Grades page text:
        - Looks for "<subject> : Semester Grade ... (xx.xx%)" or a letter grade near that label.
        - Returns {subject: percent_or_letter}, subject normalized to UPPER.
        """
        soup = BeautifulSoup(html or "", "html.parser")
        text = " ".join(soup.get_text(" ", strip=True).split())

        pat = re.compile(
            r"(?P<subject>[A-Z0-9][A-Z0-9 &/\-\.,']+?)\s*:\s*(?:Semester|Final)\s+Grade\b"
            r".{0,120}?"
            r"(?:(?P<pct>\d{1,3}(?:\.\d+)?)\s*%|(?P<letter>\b[A-F][+-]?))",
            re.IGNORECASE | re.DOTALL,
        )

        latest: Dict[str, Any] = {}
        for m in pat.finditer(text):
            subj = m.group("subject").strip()
            pct = m.group("pct")
            letter = m.group("letter")
            if pct is not None:
                try:
                    val: Any = float(pct)
                except ValueError:
                    val = pct
            elif letter:
                val = letter
            else:
                continue

            subj_norm = re.sub(r"\s+", " ", subj).strip().upper()
            latest[subj_norm] = val

        return latest

    # ---------------------- SNAPSHOT HELPERS ---------------------------------
    async def _save_mhtml(self, page, out_dir: Path, prefix: str = "grades") -> Path:
        """
        Save a complete-page MHTML snapshot (closest to 'Save Page As → Webpage, Complete').
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        client = await page.context.new_cdp_session(page)
        resp = await client.send("Page.captureSnapshot", {"format": "mhtml"})
        data = resp["data"] if isinstance(resp, dict) else resp  # handle both shapes
        mhtml_path = out_dir / f"{prefix}.mhtml"
        mhtml_path.write_text(data, encoding="utf-8")
        print(f"[IC] Wrote MHTML → {mhtml_path}")
        return mhtml_path

    async def _save_dom_htmls(self, page, out_dir: Path, prefix: str = "grades") -> Tuple[str, List[str]]:
        """
        Dump the main DOM and each frame's DOM as standalone HTML files for offline inspection.
        Returns (main_path, [frame_paths]).
        """
        out_dir.mkdir(parents=True, exist_ok=True)

        main_path = str(out_dir / f"{prefix}-MAIN.html")
        Path(main_path).write_text(await page.content(), encoding="utf-8")
        print(f"[IC] Wrote main DOM → {main_path}")

        frame_paths: List[str] = []
        for i, f in enumerate(page.frames):
            try:
                fhtml = await f.content()
            except Exception:
                continue
            fp = str(out_dir / f"{prefix}-FRAME{i}.html")
            Path(fp).write_text(fhtml, encoding="utf-8")
            short = (getattr(f, "url", "") or "").split("?")[0][-80:]
            print(f"[IC] Wrote frame DOM #{i} ({short}) → {fp}")
            frame_paths.append(fp)

        return main_path, frame_paths

    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
