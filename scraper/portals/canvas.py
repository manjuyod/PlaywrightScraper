# scraper/portals/canvas.py
from __future__ import annotations

import re
from time import monotonic
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Literal
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
      fall_year: int
      spring_year: int
      term: 'FALL' | 'SPRING'
    """
    now = datetime.now()
    m, y = now.month, now.year
    if m >= 8:
        fall_year = y
        term = "FALL"
    elif m <= 5:
        fall_year = y - 1
        term = "SPRING"
    else:
        fall_year = y
        term = "FALL"
    return {"fall_year": fall_year, "spring_year": fall_year + 1, "term": term}


def _build_term_regexes(fall_year: int, spring_year: int, term: str) -> tuple[List[re.Pattern], List[re.Pattern]]:
    yy_fall = fall_year % 100
    yy_spring = spring_year % 100
    prev_fall = fall_year - 1
    yy_prev_fall = prev_fall % 100
    sep = r"[–-]"

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
    else:
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
            await self.page.wait_for_selector(selector, timeout=timeout, state="visible")
            return True
        except PlaywrightTimeout:
            return False

    async def _container_text_for(self, a_locator) -> str:
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

    async def _dismiss_common_popups(self):
        dismiss_targets = [
            self.page.get_by_role("button", name=re.compile(r"not now", re.I)),
            self.page.get_by_role("button", name=re.compile(r"done", re.I)),
            self.page.get_by_role("button", name=re.compile(r"close", re.I)),
            self.page.get_by_role("button", name=re.compile(r"skip", re.I)),
        ]
        for target in dismiss_targets:
            try:
                if await exists(target, timeout=800):
                    await target.click(timeout=1500)
                    await self.page.wait_for_timeout(300)
            except Exception:
                pass

    async def _is_canvas_logged_in(self) -> bool:
        url = self.page.url or ""

        username_still_visible = await exists(self.page.locator("#username"), timeout=600)
        password_still_visible = await exists(self.page.locator("#password"), timeout=600)
        pseudo_user_visible = await exists(
            self.page.locator("input[name='pseudonym_session[unique_id]']"),
            timeout=600,
        )
        pseudo_pass_visible = await exists(
            self.page.locator("input[name='pseudonym_session[password]']"),
            timeout=600,
        )

        if username_still_visible or password_still_visible or pseudo_user_visible or pseudo_pass_visible:
            return False

        indicators = [
            "nav.ic-app-header__menu-list",
            "#menu",
            "[aria-label='Global Navigation']",
            "[data-testid='dashboard-options-button']",
            "[data-testid='planner-todos']",
            "[data-testid='k5-dashboard']",
            "a[href*='/courses']",
            "a[href*='/calendar']",
            "a[href*='/account']",
        ]

        for sel in indicators:
            if await self._exists(sel, timeout=1000):
                return True

        return not re.search(r"/login|signin|saml|oauth|auth", url, re.I)

    async def _has_canvas_login_error(self) -> bool:
        error_targets = [
            self.page.get_by_role("alert"),
            self.page.locator(".ic-Login-error"),
            self.page.locator(".alert"),
            self.page.locator(".error"),
            self.page.locator(".ic-flash-error"),
            self.page.locator("text=/invalid|incorrect|failed|unsuccessful|try again|username|password/i"),
        ]
        for target in error_targets:
            try:
                if await exists(target, timeout=700):
                    return True
            except Exception:
                pass
        return False

    async def _wait_for_login_result(self, timeout_ms: int = 12000) -> bool:
        """
        Wait for either:
          - a recognizable logged-in Canvas state
          - a recognizable login error
        Returns True on success, False on failure.
        Raises PlaywrightTimeout if neither becomes clear.
        """
        deadline = monotonic() + (timeout_ms / 1000)

        while monotonic() < deadline:
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=800)
            except Exception:
                pass

            if await self._is_canvas_logged_in():
                return True

            if await self._has_canvas_login_error():
                return False

            await self.page.wait_for_timeout(500)

        raise PlaywrightTimeout(f"Timed out waiting for Canvas login result (url={self.page.url})")

    async def _click_sso_entry_if_needed(self):
        """
        Some Canvas pages first show SSO buttons rather than credential fields.
        """
        native_user = self.page.locator("#username")
        native_pass = self.page.locator("#password")
        pseudo_user = self.page.locator("input[name='pseudonym_session[unique_id]']")
        pseudo_pass = self.page.locator("input[name='pseudonym_session[password]']")

        native_visible = (
            await exists(native_user, timeout=1000)
            or await exists(native_pass, timeout=1000)
            or await exists(pseudo_user, timeout=1000)
            or await exists(pseudo_pass, timeout=1000)
        )
        if native_visible:
            return

        sso_selectors = (
            "button:has-text('Log In With Google')",
            "a:has-text('Log In With Google')",
            "button:has-text('Sign in with Google')",
            "a:has-text('Sign in with Google')",
            "button:has-text('Log In With Microsoft')",
            "a:has-text('Log In With Microsoft')",
            "button:has-text('Sign in with Microsoft')",
            "a:has-text('Sign in with Microsoft')",
            "button:has-text('Microsoft')",
            "a:has-text('Microsoft')",
            "button:has-text('Google')",
            "a:has-text('Google')",
            "button:has-text('Single Sign-On')",
            "a:has-text('Single Sign-On')",
            "button:has-text('SSO')",
            "a:has-text('SSO')",
        )

        for sso_sel in sso_selectors:
            try:
                loc = self.page.locator(sso_sel).first
                if await exists(loc, timeout=800):
                    await loc.click()
                    await wait_after_nav(self.page, wait_until="domcontentloaded")
                    return
            except Exception:
                continue

    # ----------------- login -----------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.8, min=0.8, max=3),
        retry=retry_if_exception_type(PlaywrightTimeout),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None):
        """
        Fill creds, submit, and land in a valid post-login Canvas state.
        """
        try:
            if not self.login_url:
                raise LoginError("Missing login_url for Canvas")

            await self.page.goto(self.login_url, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(750)

            await self._click_sso_entry_if_needed()

            uid_sel = "#username"
            pwd_sel = "#password"

            native_user_visible = await exists(self.page.locator(uid_sel), timeout=1200)
            native_pass_visible = await exists(self.page.locator(pwd_sel), timeout=1200)

            if native_user_visible or native_pass_visible:
                await universal_login_flow(
                    self.page,
                    self.login_url,
                    self.sid,
                    self.pw,
                    uid_sel,
                    pwd_sel,
                    microsoft_callback=self.microsoft_login,
                    google_callback=self.google_login,
                    alt_sso_callback=self.alt_login,
                )
            else:
                await self.alt_login()

            login_ok = await self._wait_for_login_result(timeout_ms=14000)
            await self.raise_login_error_if(
                not login_ok,
                f"Canvas bad username/password or login failed (url={self.page.url})",
            )

            await self.post_login()

            ok = await self._is_canvas_logged_in()
            await self.raise_login_error_if(
                not ok,
                f"Canvas login did not reach a recognized post-login state (url={self.page.url})",
            )

            await self.post_login()
        except Exception as e:
            print(e)
            raise LoginError(f"Canvas login failed: {e}") from e

    async def post_login(self):
        """
        Required by workflows that expect every engine to expose post_login().
        Safe cleanup after login.
        """
        await self.page.wait_for_load_state("domcontentloaded")
        await self.page.wait_for_timeout(1000)
        await self._dismiss_common_popups()

        try:
            self._base = _origin(self.page.url)
        except Exception:
            pass

    async def alt_login(self):
        """
        Canvas pseudonym-style fallback login.
        """
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
            google_callback=self.google_login,
        )

    async def post_login(self):
        # handle student view popup
        student_tour = await exists(self.page.get_by_text('Student Tour'))
        if student_tour:
            not_now_button = self.page.get_by_role('button', name='Not Now')
            await not_now_button.click()
            done_button = self.page.get_by_role('button', name='Done')
            if await exists(done_button):
                await done_button.click()

        # ensure we are on list view
        show_grades_button = self.page.locator('[data-testid="show-my-grades-button"]')
        if await show_grades_button.count() == 0: # no show grades button, switch to list view
            await self.page.locator('[data-testid="dashboard-options-button"]').click()
            await self.page.locator('[data-testid="list-view-menu-item"]').click()

            await self.page.wait_for_selector('[data-testid="show-my-grades-button"]')
            await self.page.wait_for_timeout(1500)

    # ----------------- grades scraping -----------------

    async def fetch_grades(self) -> Dict[str, Any]:
        """
        Prefer dashboard/list view parsing first.
        Fall back to iterative course-by-course parsing.
        """
        student_tour = await exists(self.page.get_by_text("Student Tour"))
        if student_tour:
            not_now_button = self.page.get_by_role("button", name="Not Now")
            await not_now_button.click()
            done_button = self.page.get_by_role("button", name="Done")
            if await exists(done_button):
                await done_button.click()

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

    async def parse_grades_from_list_view(self) -> dict[str, float]:
        parsed: dict[str, float] = {}

        show_grades_button = self.page.locator('[data-testid="show-my-grades-button"]')
        if await show_grades_button.count() > 0:
            await show_grades_button.click()
        else:
            await self.page.locator('[data-testid="dashboard-options-button"]').click()
            await self.page.locator('[data-testid="list-view-menu-item"]').click()

            await self.page.wait_for_selector('[data-testid="show-my-grades-button"]')
            await self.page.wait_for_timeout(1500)
            show_grades_button = self.page.locator('[data-testid="show-my-grades-button"]')

            if await show_grades_button.count() > 0:
                await show_grades_button.click()
            else:
                return parsed

        await self.page.wait_for_selector('[data-testid="my-grades-score"]', state="attached")

        course_grades = await self.page.locator('[data-testid="my-grades-score"]').all()
        count = len(course_grades)
        print(f"Found {count} grades")

        for i in range(count):
            course_grade = course_grades[i]
            course_card = course_grade.locator("xpath=..")
            course = await course_card.get_by_role("link").inner_text()
            grade_str: str = await course_grade.inner_text()
            if grade_str.lower() == "no grade":
                continue
            print("Canvas: Grade found", grade_str)
            grade = canonicalize_grade(grade_str)
            parsed[course] = grade

        return parsed

    async def parse_grades_iterative(self) -> dict[str, float]:
        """
        Course-by-course fallback. Returns a normalized course->grade mapping.
        """
        try:
            cur = self.page.url
            if cur:
                self._base = _origin(cur)
        except Exception:
            pass

        term_context = _term_context_from_today()
        allow_regexes, deny_regexes = _build_term_regexes(
            term_context.get("fall_year"),
            term_context.get("spring_year"),
            term_context.get("term"),
        )

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

        await self.page.wait_for_timeout(400)

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

            container_text = await self._container_text_for(a)
            if not _matches_current_term(container_text, allow_regexes, deny_regexes):
                continue

            full = href if href.startswith("http") else urljoin(self._base, href)
            seen.add(cid)
            hrefs.append(full)

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

                container_text = await self._container_text_for(a)
                if not _matches_current_term(container_text, allow_regexes, deny_regexes):
                    continue

                full = href if href.startswith("http") else urljoin(self._base, href)
                seen.add(cid)
                hrefs.append(full)

        results: dict[str, float] = {}

        for course_url in hrefs:
            cid_match = re.search(r"/courses/(\d+)", course_url)
            cid = cid_match.group(1) if cid_match else "unknown"
            course_name = f"Course {cid}"

            try:
                await self._goto(course_url)

                try:
                    title = await self.page.locator("h1, .course-title, [data-testid='course-title']").first.text_content()
                    if title:
                        course_name = title.strip()
                except Exception:
                    pass

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

                if "final_percent" in course_result:
                    try:
                        results[course_name] = float(course_result["final_percent"])
                    except Exception:
                        pass

            except TimeoutError:
                print(f"[Canvas] Timeout while scraping course {course_name} ({cid})")
            except Exception as e:
                print(f"[Canvas] Error scraping course {course_name} ({cid}): {e}")

        return results

    # ----------------- HTML parsing heuristics -----------------

    def _parse_canvas_grades_html(self, html: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True).lower()

        pm = re.search(r"(?:total|current\s*grade|final)\s*[:\-]?\s*(\d{1,3}(?:\.\d+)?)\s*%", text)
        percent = pm.group(1) if pm else None

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
                percent = m_pct.group(1)
            m_pts = re.search(r"(\d{1,5}(?:\.\d+)?)\s*/\s*(\d{1,5}(?:\.\d+)?)", total_value)
            if m_pts:
                points = f"{m_pts.group(1)}/{m_pts.group(2)}"

        out: Dict[str, Any] = {}
        if percent:
            try:
                out["final_percent"] = float(percent)
            except ValueError:
                out["final_percent"] = percent
        if points:
            out["points"] = points
        if not out:
            out["note"] = "No final/total grade detected"

        return out

    async def get_agenda(self, get: Literal["upcoming", "missing"] = "upcoming"):
        await self.page.wait_for_load_state("domcontentloaded")
        soup = await self.get_soup()

        agenda: dict[str, list[tuple]] = {}
        await self.page.locator('[data-testid="dashboard-options-button"]').click()
        await self.page.locator('[data-testid="list-view-menu-item"]').click()
        await self.page.wait_for_timeout(1500)

        try:
            all_days = soup.find_all("div", attrs={"data-testid": "day"})

            print(f"Found {len(all_days)} days")
            today_passed = False

            for i, day_block in enumerate(all_days):
                if i > 7:
                    break

                today_reached = today_passed
                date_elem = day_block.select_one('[data-testid="today-date"]')

                if not today_passed:
                    if date_elem is not None:
                        today_reached = True
                    else:
                        continue

                assert today_reached

                if today_passed:
                    date_elem = day_block.select_one('[data-testid="not-today"]')
                else:
                    today_passed = True

                assert date_elem is not None
                date_text = date_elem.get_text(strip=True)
                day, _ = reconcile_day_time(date_text, reference=datetime.now())
                due_date = day.strftime("%m/%d/%Y")

                assignments: list[tuple] = []
                class_groups = day_block.select("div.planner-grouping")
                print(f"Found {len(class_groups)} classes with assignments due on {due_date}")

                for course in class_groups:
                    title_elem = course.select_one("span.Grouping-styles__title")
                    class_title = title_elem.get_text(strip=True) if title_elem else None
                    print("-", class_title)

                    if class_title is None:
                        continue

                    assignment_items = course.select('div[data-testid="planner-item-raw"]')
                    print(f"\t{len(assignment_items)} due")

                    for assignment in assignment_items:
                        a = assignment.select_one("a")
                        if not a:
                            continue

                        title_span = a.select_one('span[aria-hidden="true"]')
                        assignment_title = title_span.get_text(strip=True) if title_span else "Unknown Assignment"

                        due_time_elem = assignment.select_one(".PlannerItem-styles__due span[aria-hidden='true']")
                        due_time = due_time_elem.get_text(strip=True) if due_time_elem else None

                        print("\t-", assignment_title)
                        assignments.append((class_title, assignment_title, due_time))

                if len(assignments) > 0:
                    agenda[due_date] = assignments

        except Exception as e:
            print(f"Error while gathering the agenda {type(e)}: {e}")
        finally:
            return agenda