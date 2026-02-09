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

from .utils import *


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

            # Native Canvas selectors
            uid_sel = "input[name='pseudonym_session[unique_id]']"
            pwd_sel = "input[name='pseudonym_session[password]']"

            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                uid_sel,
                pwd_sel,
                microsoft_callback=self.microsoft_login,
                google_callback=self.google_login
            )

            await wait_after_nav(self.page, wait_until="domcontentloaded")
            nav_ok = await self._exists("nav.ic-app-header__menu-list, #menu, [aria-label='Global Navigation']", timeout=10000)
            await self.raise_login_error_if(not nav_ok, "Canvas login did not reach dashboard/global nav")

        except Exception as e:
            print(e)
            raise LoginError(f"Canvas login failed: {e}") from e

    # ----------------- grades scraping -----------------
    async def fetch_grades(self) -> Dict[str, Any]:
        """
        Flow:
          - Open 'Courses' tray (or fall back to /courses)
          - Collect '/courses/<id>' links whose row/card text matches CURRENT term (allow AND NOT deny)
          - For each course, open 'Grades' and parse final/total grade
        """
        # Ensure base reflects post-login host
        # TODO Handle the 'student welcome' popup that sometimes blocks
        try:
            parsed = await self.parse_grades_from_list_view()
            if len(parsed) == 0:
                parsed = await self.parse_grades_iterative()
            print(parsed)
            return parsed
        except Exception as e:
            # import traceback
            # traceback.print_exc()
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
                if grade_str.lower() == "no grade":
                    continue
                print("Canvas: Grade found", grade_str)
                grade = canonicalize_grade(grade_str)
                parsed[course] = grade
                # print(course, grade_str)
            # print(grade_cards)
            return parsed
        finally:
            pass

    async def parse_grades_iterative(self):
        try:
            cur = self.page.url
            if cur:
                self._base = _origin(cur)
        except Exception:
            pass


        term_context = _term_context_from_today()
        allow_regexes, deny_regexes = _build_term_regexes(term_context.get('fall_year'), term_context.get('spring_year'), term_context.get('term'))


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
            if not _matches_current_term(container_text, allow_regexes, deny_regexes):
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
                if not _matches_current_term(container_text, allow_regexes, deny_regexes):
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

    async def get_agenda(self):
        await self.page.wait_for_load_state('domcontentloaded')
        soup = await self.get_soup()
        agenda: dict[str, list[tuple]] = {} # dict like {Date: [(class, assignment), ...]}
        try:
            all_days = soup.find_all("div", attrs={"data-testid": "day"})

            print(f"Found {len(all_days)} days")
            today_passed = False
            for i, day_block in enumerate(all_days):
                if i > 7: break
                today_reached = today_passed
                # parse the date
                date_elem = day_block.select_one('[data-testid="today-date"]')
                if not today_passed: # continue until today's date
                    if date_elem is not None:
                        today_reached = True
                    else:
                        continue
                
                assert today_reached
                if today_passed:
                    date_elem = day_block.select_one('[data-testid="not-today"]')
                else: today_passed = True
                    
                assert date_elem is not None
                date = date_elem.get_text(strip=True)
                # gather assignments
                assignments: list[tuple] = []
                class_groups = day_block.select("div.planner-grouping")
                print(f"Found {len(class_groups)} classes with assignments due on {date}")
                # iterate on the classes for this day
                for course in class_groups:
                    class_title = course.select_one("span.Grouping-styles__title").get_text(strip=True)
                    print("-", class_title)
                    if class_title is None:
                        continue
                    assignment_items = course.select('div[data-testid="planner-item-raw"]')
                    print(f"\t{len(assignment_items)} due")
                    # iterate on assignments due for this class
                    for assignment in assignment_items:
                        a = assignment.select_one('a')
                        assignment_title = a.select_one('span[aria-hidden="true"]').get_text(strip=True)

                        due_time_elem = assignment.select_one(".PlannerItem-styles__due span[aria-hidden='true']")
                        due_time = due_time_elem.get_text(strip=True) if due_time_elem else None

                        print("\t-", assignment_title)
                        assignments.append( (class_title, assignment_title, due_time) )

                agenda[date] = assignments
                # i += 1
        except Exception as e:
            print(f"Error while gathering the agenda {type(e)}: {e}")
        finally:
            from pprint import pprint
            pprint(agenda, sort_dicts=False)
            await self.page.pause()
            return agenda