# scraper/portals/canvas.py
from __future__ import annotations

import os
import re
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from playwright.async_api import Page, TimeoutError

from .base import PortalEngine, PlaywrightTimeout
from . import register_portal, LoginError


# --------------------- utilities ---------------------

def _origin(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else url


def _term_context_from_today() -> dict:
    """
    Determine current academic term context.

    Returns dict with:
      fall_year: int       (academic cycle starts in this fall)
      spring_year: int     (fall_year + 1)
      term: 'FALL' | 'SPRING'
    """
    now = datetime.now()
    m, y = now.month, now.year
    if m >= 8:            # Aug–Dec → Fall of current year
        fall_year = y
        term = "FALL"
    elif m <= 5:          # Jan–May → Spring of previous fall year
        fall_year = y - 1
        term = "SPRING"
    else:                 # Jun–Jul → prep for upcoming Fall
        fall_year = y
        term = "FALL"
    return {"fall_year": fall_year, "spring_year": fall_year + 1, "term": term}


def _build_term_regexes(fall_year: int, spring_year: int, term: str) -> tuple[List[re.Pattern], List[re.Pattern]]:
    """
    Build allow/deny regexes for CURRENT cycle.

    For FALL term:
      ALLOW:   Fall {fall_year}, {fall_year}–{spring_year}, {yy_fall}–{yy_spring}
      DENY:    Spring {fall_year}, {fall_year-1}–{fall_year}, {yy_prev_fall}–{yy_fall}

    For SPRING term:
      ALLOW:   Spring {spring_year}, {fall_year}–{spring_year}, {yy_fall}–{yy_spring}
      DENY:    Fall {fall_year-1}, {fall_year-1}–{fall_year}, {yy_prev_fall}–{yy_fall}
    """
    yy_fall = fall_year % 100
    yy_spring = spring_year % 100
    prev_fall = fall_year - 1
    yy_prev_fall = prev_fall % 100
    sep = r"[–-]"  # en dash or hyphen

    if term == "FALL":
        allow = [
            re.compile(rf"\b(Fall|Autumn)\s*{fall_year}\b", re.I),
            re.compile(rf"\b{fall_year}\s*{sep}\s*{spring_year}\b", re.I),
            re.compile(rf"\b{yy_fall}\s*{sep}\s*{yy_spring}\b", re.I),
        ]
        deny = [
            re.compile(rf"\bSpring\s*{fall_year}\b", re.I),
            re.compile(rf"\b{prev_fall}\s*{sep}\s*{fall_year}\b", re.I),
            re.compile(rf"\b{yy_prev_fall}\s*{sep}\s*{yy_fall}\b", re.I),
        ]
    else:  # SPRING
        allow = [
            re.compile(rf"\bSpring\s*{spring_year}\b", re.I),
            re.compile(rf"\b{fall_year}\s*{sep}\s*{spring_year}\b", re.I),
            re.compile(rf"\b{yy_fall}\s*{sep}\s*{yy_spring}\b", re.I),
        ]
        deny = [
            re.compile(rf"\b(Fall|Autumn)\s*{prev_fall}\b", re.I),
            re.compile(rf"\b{prev_fall}\s*{sep}\s*{fall_year}\b", re.I),
            re.compile(rf"\b{yy_prev_fall}\s*{sep}\s*{yy_fall}\b", re.I),
        ]
    return allow, deny


def _matches_current_term(text: str, allow: List[re.Pattern], deny: List[re.Pattern]) -> bool:
    t = text or ""
    if any(r.search(t) for r in deny):
        return False
    return any(r.search(t) for r in allow)


# --------------------- engine ---------------------

@register_portal("canvas")
class CanvasEngine(PortalEngine):
    """
    Expects runner to pass:
      - username (Student.P1Username)
      - password (Student.P1Password)
      - login_url (Student.Portal1), e.g. https://<tenant>.instructure.com/login/canvas
    """

    def __init__(
        self,
        page: Page,
        username: str,
        password: str,
        *,
        student_name: Optional[str] = None,
        login_url: Optional[str] = None,
    ):
        super().__init__(page, username, password, login_url=login_url, student_name=student_name)

        # Ensure attributes used here exist (don't shadow self.login)
        self.page = page
        self.username = username
        self.password = password
        self.user = username

        # Defaults + base
        self.login_url = login_url
        self._base = _origin(self.login_url)

        # Term context from date or optional override
        ctx = _term_context_from_today()
        # Manual override (rarely needed): CANVAS_TERM = FALL|SPRING, CANVAS_FALL_YEAR=YYYY
        term_override = os.getenv("CANVAS_TERM")
        fall_override = os.getenv("CANVAS_FALL_YEAR")
        if term_override in ("FALL", "SPRING") and fall_override and fall_override.isdigit():
            ctx = {"fall_year": int(fall_override), "spring_year": int(fall_override) + 1, "term": term_override}

        self.fall_year = ctx["fall_year"]
        self.spring_year = ctx["spring_year"]
        self.term = ctx["term"]
        self._allow_regexes, self._deny_regexes = _build_term_regexes(self.fall_year, self.spring_year, self.term)
        print(f"[CanvasEngine] Term filter: {self.term} {self.fall_year} (cycle {self.fall_year}-{self.spring_year})")

    # ----------------- helpers -----------------
    async def _goto(self, url: str):
        await self.page.goto(url, wait_until="domcontentloaded")

    async def _click(self, selector: str, *, timeout: int = 15000):
        await self.page.click(selector, timeout=timeout)

    async def _fill(self, selector: str, value: str, *, timeout: int = 15000, delay: float = 0.0):
        await self.page.fill(selector, value, timeout=timeout)
        if delay:
            await asyncio.sleep(delay)

    async def _exists(self, selector: str, *, timeout: int = 3000) -> bool:
        try:
            # print(f'locating {selector} |\t')
            await self.page.wait_for_selector(selector, timeout=timeout, state="visible")
            # print('Elem exists')
            return True
        except PlaywrightTimeout:
            # print('Failed to find elem')
            return False

    async def _container_text_for(self, a_locator) -> str:
        """Get nearby container text (row/card/list item) for a course link (works for 'All Courses' and dashboard)."""
        for xp in (
            "xpath=ancestor::tr[1]",
            "xpath=ancestor::li[1]",
            "xpath=ancestor::div[contains(@class,'ic-DashboardCard')][1]",
            "xpath=ancestor::*[self::div or self::li or self::tr][1]",
        ):
            try:
                t = await a_locator.locator(xp).inner_text()
                if t:
                    return t
            except Exception:
                continue
        try:
            return (await a_locator.inner_text()) or ""
        except Exception:
            return ""

    # ----------------- login -----------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.8, min=0.8, max=3),
        retry=retry_if_exception_type(PlaywrightTimeout),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None):
        """Fill creds, submit, land with global nav."""
        try:
            if not self.login_url:
                raise LoginError("Missing login_url for Canvas")

            await self._goto(self.login_url)
            await self.page.wait_for_timeout(3000)

            if 'microsoft' in self.page.url:
                await self.microsoft_login()
            elif 'google' in self.page.url:
                await self.google_signin()
            else:
                # Native Canvas selectors
                uid_sel = "input[name='pseudonym_session[unique_id]']"
                pwd_sel = "input[name='pseudonym_session[password]']"

                # Generic SSO fallbacks
                generic_user = "input[type='email'], input[name='username'], input#username, input[autocomplete='username']"
                generic_pass = "input[type='password'], input[name='password'], input#password, input[autocomplete='current-password']"

                # Fill fields
                filled = False
                if await self._exists(uid_sel, timeout=4000) and await self._exists(pwd_sel, timeout=4000):
                    await self._fill(uid_sel, self.username)
                    await self._fill(pwd_sel, self.password)
                    filled = True
                else:
                    print('Non Canvas login, use fallback')
                    if await self._exists(generic_user, timeout=2000):
                        await self._fill(generic_user, self.username)
                    if await self._exists(generic_pass, timeout=2000):
                        await self._fill(generic_pass, self.password)
                        print('Fallback successful')
                        filled = True

                if not filled:
                    raise LoginError("Could not locate username/password fields")

                # Robust submit
                submit_selectors = [
                    # "button[type='submit']",
                    # "button:has-text('Log In')",
                    # "button:has-text('Login')",
                    # "button:has-text('Sign in')",
                    "input[type='submit']",
                ]
                for sel in submit_selectors:
                    if await self._exists(sel, timeout=1200):
                        await self._click(sel)
                        break
                else:
                    try:
                        await self.page.focus("input[type='password'], input[name='pseudonym_session[password]'], input#password")
                        await self.page.keyboard.press("Enter")
                    except Exception:
                        pass

                # Interstitials
                await self.page.wait_for_load_state("domcontentloaded")
            nav_ok = await self._exists("nav.ic-app-header__menu-list, #menu, [aria-label='Global Navigation']", timeout=10000)
            # if not nav_ok:
            #     for btn_txt in ("Continue", "Yes", "Accept", "Allow", "Skip"):
            #         if await self._exists(f"button:has-text('{btn_txt}')", timeout=1200):
            #             await self._click(f"button:has-text('{btn_txt}')")
            #             break
            #     nav_ok = await self._exists("nav.ic-app-header__menu-list, [aria-label='Global Navigation']", timeout=8000)

            if not nav_ok:
                raise LoginError("Canvas login did not reach dashboard/global nav")

            # After login, recompute base in case of redirect
            try:
                current = self.page.url
                if current:
                    self._base = _origin(current)
            except Exception:
                pass

        except Exception as e:
            print(e)
            raise LoginError(f"Canvas login failed: {e}") from e

    # ----------------- grades scraping -----------------
    async def fetch_grades(self) -> Dict[str, Any]:
        # """
        # Flow:
        #   - Open 'Courses' tray (or fall back to /courses)
        #   - Collect '/courses/<id>' links whose row/card text matches CURRENT term (allow AND NOT deny)
        #   - For each course, open 'Grades' and parse final/total grade
        # """
        # Ensure base reflects post-login host
        try:
            parsed = await self.parse_grades_from_list_view()
            if len(parsed) == 0:
                parsed = await self.parse_grades_iterative()
            print(parsed)
            return parsed
        except Exception as e:
            print(f"[Canvas] Error: {e}")
            raise
        finally:
            pass

    async def parse_grades_from_list_view(self) -> dict[str, float]:
        try:
            parsed: dict[str, float] = {}
            # 1. show grades
            show_grades_button = self.page.locator('[data-testid="show-my-grades-button"]')
            if await show_grades_button.count() > 0:
                await show_grades_button.click()
            else: # switch views then try again
                await self.page.locator('[data-testid="dashboard-options-button"]').click()
                await self.page.locator('[data-testid="list-view-menu-item"]').click()

                await self.page.wait_for_selector('[data-testid="show-my-grades-button"]')
                await self.page.wait_for_timeout(1500)
                show_grades_button = self.page.locator('[data-testid="show-my-grades-button"]')

                if await show_grades_button.count() > 0:
                    await show_grades_button.click()
                else:
                    print('failed to switch to a valid view')
            # await self.page.wait_for_timeout(2000) # small wait to allow population
            await self.page.wait_for_selector('[data-testid="my-grades-score"]', state='attached')
            # 2. parse
            course_grades = await self.page.locator('[data-testid="my-grades-score"]').all()
            count = len(course_grades)
            print(f"Found {count} grades")
            # print("Course cards: ", course_cards)
            for i in range(count):
                course_grade = course_grades[i]
                course_card = course_grade.locator('xpath=..') # nav to the parent, we got a list of grades which are inner elems
                course = await course_card.get_by_role('link').inner_text()
                grade_str: str = await course_grade.inner_text()
                if '%' in grade_str:
                    grade = float(grade_str.replace('%', ''))
                else: continue # NaN

                parsed[course] = grade
                # print(course, grade_str)
            # print(grade_cards)
            return parsed
        finally:
            pass
            # await self.page.pause()

    async def parse_grades_iterative(self):
        try:
            cur = self.page.url
            if cur:
                self._base = _origin(cur)
        except Exception:
            pass

        # Open the "Courses" tray
        opened = False
        try:
            await self.page.get_by_role("link", name=re.compile(r"^Courses?$", re.I)).click(timeout=5000)
            opened = True
        except Exception:
            for sel in (
                "a.ic-app-header__menu-list-item__link[aria-label='Courses']",
                "[aria-label='Global Navigation'] a[aria-label='Courses']",
                "nav.ic-app-header__menu-list a:has-text('Courses')",
            ):
                if await self._exists(sel, timeout=2000):
                    await self._click(sel)
                    opened = True
                    break
        if not opened:
            await self._goto(urljoin(self._base, "/courses"))

        await self.page.wait_for_timeout(400)  # small settle

        # Gather links and filter by CURRENT term patterns
        course_link_locator = "a[href*='/courses/']:not([href*='/courses/new'])"
        links = await self.page.locator(course_link_locator).all()

        hrefs: List[str] = []
        seen = set()
        for a in links:
            href = (await a.get_attribute("href")) or ""
            m = re.search(r"/courses/(\d+)", href)
            if not m:
                continue
            cid = m.group(1)
            if cid in seen:
                continue

            container_text = (await self._container_text_for(a))
            if not _matches_current_term(container_text, self._allow_regexes, self._deny_regexes):
                continue

            full = href if href.startswith("http") else urljoin(self._base, href)
            seen.add(cid)
            hrefs.append(full)

        # Fallback: go to /courses if tray yielded nothing
        if not hrefs:
            await self._goto(urljoin(self._base, "/courses"))
            await self.page.wait_for_timeout(250)
            links = await self.page.locator(course_link_locator).all()
            for a in links:
                href = (await a.get_attribute("href")) or ""
                m = re.search(r"/courses/(\d+)", href)
                if not m:
                    continue
                cid = m.group(1)
                if cid in seen:
                    continue
                container_text = (await self._container_text_for(a))
                if not _matches_current_term(container_text, self._allow_regexes, self._deny_regexes):
                    continue
                full = href if href.startswith("http") else urljoin(self._base, href)
                seen.add(cid)
                hrefs.append(full)

        results: List[Dict[str, Any]] = []

        # Visit each course, then click "Grades"
        for course_url in hrefs:
            cid_match = re.search(r"/courses/(\d+)", course_url)
            cid = cid_match.group(1) if cid_match else "unknown"
            course_name = f"Course {cid}"

            try:
                await self._goto(course_url)

                # Friendly title, if present
                try:
                    title = await self.page.locator("h1, .course-title, [data-testid='course-title']").first.text_content()
                    if title:
                        course_name = title.strip()
                except Exception:
                    pass

                # Click Grades (or direct /grades)
                grades_clicked = False
                try:
                    await self.page.get_by_role("link", name=re.compile(r"^Grades?$", re.I)).click(timeout=3000)
                    grades_clicked = True
                except Exception:
                    for sel in (
                        "nav[aria-label='Course Navigation'] a:has-text('Grades')",
                        "a[aria-label='Grades']",
                        "a[href$='/grades']",
                    ):
                        if await self._exists(sel, timeout=1500):
                            await self._click(sel)
                            grades_clicked = True
                            break

                if not grades_clicked and cid != "unknown":
                    await self._goto(urljoin(self._base, f"/courses/{cid}/grades"))

                await self.page.wait_for_timeout(300)

                html = await self.page.content()
                course_result = self._parse_canvas_grades_html(html)
                course_result.update({"course_id": cid, "course_name": course_name, "grades_url": self.page.url})
                results.append(course_result)

            except TimeoutError:
                results.append({"course_id": cid, "course_name": course_name, "grades_url": self.page.url, "error": "Timeout"})
            except Exception as e:
                results.append({"course_id": cid, "course_name": course_name, "grades_url": self.page.url, "error": str(e)})
    # ----------------- HTML parsing heuristics -----------------
    def _parse_canvas_grades_html(self, html: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True).lower()

        pm = re.search(r"(?:total|current\s*grade|final)\s*[:\-]?\s*(\d{1,3}(?:\.\d+)?)\s*%", text)
        percent = pm.group(1) if pm else None  # strip the percent sign

        pmm = re.search(r"(\d{1,5}(?:\.\d+)?)\s*/\s*(\d{1,5}(?:\.\d+)?)", text)
        points = f"{pmm.group(1)}/{pmm.group(2)}" if pmm else None

        total_value = None
        for row in soup.select("table tr"):
            cells = [c.get_text(" ", strip=True) for c in row.select("th,td")]
            if not cells:
                continue
            if re.search(r"\b(total|final)\b", cells[0], re.I):
                for c in reversed(cells[1:]):
                    if c:
                        total_value = c
                        break
                break

        if total_value:
            m_pct = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", total_value)
            if m_pct:
                percent = m_pct.group(1)  # normalized, no '%'
            m_pts = re.search(r"(\d{1,5}(?:\.\d+)?)\s*/\s*(\d{1,5}(?:\.\d+)?)", total_value)
            if m_pts:
                points = f"{m_pts.group(1)}/{m_pts.group(2)}"

        out: Dict[str, Any] = {}
        if percent:
            # Convert string to float for consistent numeric type
            try:
                out["final_percent"] = float(percent)
            except ValueError:
                out["final_percent"] = percent
        if points:
            out["points"] = points
        if not out:
            out["note"] = "No final/total grade detected"

        return out
