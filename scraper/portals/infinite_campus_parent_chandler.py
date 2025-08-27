# scraper/portals/infinite_campus_parent_chandler.py

from __future__ import annotations
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple, List

from bs4 import BeautifulSoup  # type: ignore
from scraper.portals.base import PortalEngine  # type: ignore
from scraper.portals import register_portal  # type: ignore
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


@register_portal("infinite_campus_parent_chandler")
class InfiniteCampus(PortalEngine):
    LOGIN = "https://chandleraz.infinitecampus.org/campus/portal/parents/chandler.jsp"
    HOME_WRAPPER = (
        "https://chandleraz.infinitecampus.org/campus/nav-wrapper/parent/portal/parent/home?appName=chandler"
    )
    GRADEBOOK = (
        "https://chandleraz.infinitecampus.org/campus/nav-wrapper/parent/portal/parent/grades?appName=chandler"
    )
    LOGOFF = (
        "https://chandleraz.infinitecampus.org/campus/portal/parents/chandler.jsp?status=logoff"
    )

    # Where to dump snapshots (they will be deleted after parsing)
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
        await self.page.wait_for_url(
            lambda u: "parent/home" in u or "nav-wrapper" in u, timeout=15_000
        )
        await self.page.wait_for_load_state("networkidle")
        await self.page.wait_for_timeout(1500)  # small hard wait for Angular to attach
        print("[IC] Logged in and on parent/home.")

    # ---------------------- FETCH (Grades only, scorched earth) ---------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """
        Full scorched-earth:
        1) Navigate to Grades
        2) Save MHTML + MAIN + every FRAME to disk
        3) Parse saved DOMs (frame-first, then main)
        4) Delete all saved files
        """
        # Always go straight to the gradebook
        await self.page.goto(self.GRADEBOOK, wait_until="domcontentloaded")
        await self.page.wait_for_load_state("networkidle")
        await self.page.wait_for_timeout(800)

        # Try to select student if a switcher exists
        first_name = (getattr(self, "student_name", None) or "").strip()
        if first_name:
            try:
                btn = self.page.locator(
                    '[aria-label*="Student"], [data-cy*="student"], button:has-text("Student")'
                )
                if await btn.first.is_visible():
                    await btn.first.click()
                    await self.page.wait_for_timeout(200)
                opt = self.page.locator(f'text=/^{re.escape(first_name)}/i')
                if await opt.first.is_visible():
                    await opt.first.click()
                    await self.page.wait_for_timeout(400)
            except Exception:
                # Non-fatal if no switcher or failure
                pass

        session_prefix = f"IC_CHANDLER_GRADES-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        mhtml_path: Optional[Path] = None
        main_path: Optional[str] = None
        frame_paths: List[str] = []

        try:
            # 2) Snapshots
            try:
                mhtml_path = await self._save_mhtml(self.page, self.OUT_DIR, prefix=session_prefix)
            except Exception as e:
                print(f"[IC] MHTML snapshot failed (non-fatal): {e!r}")

            main_path, frame_paths = await self._save_dom_htmls(self.page, self.OUT_DIR, prefix=session_prefix)
            print(f"[IC] Saved {1 + len(frame_paths)} HTML files to {self.OUT_DIR} (session={session_prefix}).")

            # 3) Parse saved DOMs (prioritize frames)
            combined: Dict[str, Any] = {}

            # 🔒 ONLY parse FRAME #1; fall back to MAIN if it's missing
            targets: List[str] = []
            if len(frame_paths) > 1:
                targets = [frame_paths[1]]   # ← Only frame 1
                print(f"[IC] Parsing only frame #1: {frame_paths[1]}")
            else:
                print("[IC] Frame #1 not found; will try main DOM instead.")

            # Parse the chosen target frame
            for fp in targets:
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
                    print(f"[IC] Parsed {len(part)} subjects from frame #1.")
                    combined.update(part)

            # If nothing from frames, try main
            if not combined and main_path:
                try:
                    html = Path(main_path).read_text(encoding="utf-8", errors="ignore")
                    part = (
                        self._parse_semester_from_collapsible_cards(html)
                        or self._parse_semester_from_grades_table(html)
                        or self._parse_semester_from_grades_view(html)
                    )
                    if part:
                        print(f"[IC] Parsed {len(part)} subjects from main DOM.")
                        combined.update(part)
                except Exception as e:
                    print(f"[IC] Could not read main file {main_path!r}: {e!r}")

            print(f"[IC] Combined subjects: {len(combined)}")
            if combined:
                print("[IC] Sample:", list(combined.items())[:3])

            return {"parsed_grades": combined}

        finally:
            # 4) Delete all saved files
            try:
                if mhtml_path:
                    mhtml_path.unlink(missing_ok=True)
                if main_path:
                    Path(main_path).unlink(missing_ok=True)
                for fp in frame_paths:
                    Path(fp).unlink(missing_ok=True)
                print(f"[IC] Cleaned up saved files for session={session_prefix}.")
            except Exception as e:
                print(f"[IC] Cleanup failed (non-fatal): {e!r}")

    # ---------------------- GRADES PARSERS ------------------------------------
    def _parse_semester_from_collapsible_cards(self, html: str) -> Dict[str, Any]:
        """
        Parse 'collapsible-card grades__card' layout:
        <h4>ENGLISH 9 - C</h4>  <-- subject (strip trailing ' - C', strip leading 'IN ')
        Row whose LEFT label is a recognized "* Grade" (Semester/Final/Term/Quarter),
        explicitly skipping "Mid Quarter Progress".
        Prefer percent if present (e.g., "(68.59%)") over letter.
        Returns {SUBJECT (UPPER): value}.
        """
        soup = BeautifulSoup(html or "", "html.parser")
        out: Dict[str, Any] = {}

        # Accept more variants; skip mid-quarter progress explicitly
        LABEL_RE = re.compile(r"\b(?:Semester|Final|Term|Quarter)\s+Grade\b", re.I)
        SKIP_RE  = re.compile(r"\bMid\s+Quarter\s+Progress\b", re.I)

        cards = soup.select("div.collapsible-card.grades__card")
        for card in cards:
            # Subject from header
            h = card.select_one(".collapsible-card__header h4")
            subject_raw = h.get_text(" ", strip=True) if h else ""
            if not subject_raw:
                continue

            # Normalize subject like: "IN English 9 - C" -> "English 9"
            subject = re.sub(r"^\s*IN\s+", "", subject_raw, flags=re.I)
            subject = re.sub(r"\s+-\s+[A-F][+-]?$", "", subject).strip()
            subject_norm = subject.upper()

            # Find row whose left label matches, but not "Mid Quarter Progress"
            for row in card.select("div.grades__row"):
                left = row.select_one(".grades__flex-row__item--left")
                left_txt = left.get_text(" ", strip=True) if left else ""
                if SKIP_RE.search(left_txt):
                    continue
                if not LABEL_RE.search(left_txt):
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
                    val = m_letter.group(1) if m_letter else (right_txt or None)

                if val is not None:
                    out[subject_norm] = val

        return out

    def _parse_semester_from_grades_table(self, html: str) -> Dict[str, Any]:
        """
        Parse Grades from table/grid markup by column headers.
        Looks for a course column and a grade column among broader variants:
        'Semester Grade', 'Final Grade', 'Term Grade', 'Quarter Grade', 'In-Progress', 'S1', 'S2', 'Q1', 'Q2', 'Grade'
        Returns {SUBJECT (UPPER): value} where value is float% if parsable else letter/string.
        """
        soup = BeautifulSoup(html or "", "html.parser")

        def norm(txt: str) -> str:
            return re.sub(r"\s+", " ", (txt or "").strip())

        out: Dict[str, Any] = {}

        # Any <table> or ARIA role=table
        tables = soup.select("table") or soup.select('[role="table"]')
        if not tables:
            return out

        grade_keys = [
            "semester grade", "final grade", "term grade", "quarter grade",
            "in-progress", "posted semester", "posted final",
            "s1", "s2", "q1", "q2", "grade"
        ]

        for tbl in tables:
            # headers might be thead/th or ARIA columnheaders
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
                if grade_idx is None and any(k in h_low for k in grade_keys):
                    grade_idx = i

            if grade_idx is None or course_idx is None:
                continue

            rows = tbl.select("tbody tr")
            if not rows:
                rows = tbl.select('[role="rowgroup"] [role="row"]')

            for r in rows:
                cells = r.select("td")
                if not cells:
                    cells = r.select('[role="gridcell"]')
                if len(cells) <= max(course_idx, grade_idx):
                    continue

                course = norm(cells[course_idx].get_text(" ", strip=True))
                grade_raw = norm(cells[grade_idx].get_text(" ", strip=True))
                if not course or not grade_raw:
                    continue

                # Normalize subject: drop leading "IN " and trailing " - X"
                course = re.sub(r"^\s*IN\s+", "", course, flags=re.I)
                course = re.sub(r"\s+-\s+[A-F][+-]?$", "", course)

                # Prefer percent (e.g., "(68.59%)" captured as 68.59) over letter
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

    def _parse_semester_from_grades_view(self, html: str) -> Dict[str, Any]:
        """
        Flexible parser for Grades text:
        - Looks for "<subject> : Semester/Final Grade ... (xx.xx%)" or a letter grade near that label.
        - Returns {subject (UPPER): percent_or_letter}
        """
        soup = BeautifulSoup(html or "", "html.parser")
        text = " ".join(soup.get_text(" ", strip=True).split())

        pat = re.compile(
            r"(?P<subject>[A-Z0-9][A-Z0-9 &/\-\.,']+?)\s*:\s*"
            r"(?:(?:Semester|Final|Term|Quarter|In[-\s]?Progress|Posted(?:\s+(?:Semester|Final))?|S1|S2|Q1|Q2))\s+Grade\b"
            r".{0,160}?"
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
