# scraper/portals/canvas.py
from __future__ import annotations

import re
from time import monotonic
import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from playwright.async_api import TimeoutError

from scraper.agenda_contract import AgendaRecord

from .base import GradeMap, LoginError, PortalEngine, PlaywrightTimeout
from .canvas_agenda import collect_canvas_agenda
from .utils import exists, canonicalize_course_title, canonicalize_grade, wait_after_nav, universal_login_flow


# --------------------- utilities ---------------------

class TermContext(TypedDict):
    fall_year: int
    spring_year: int
    term: str


_CANVAS_ENTRY_HOST = "husd.instructure.com"
_CANVAS_HOST_SUFFIX = "instructure.com"
_CANVAS_TRANSIT_HOST = "sso.canvaslms.com"
_MICROSOFT_LOGIN_HOST = "login.microsoftonline.com"


class CanvasTrustError(LoginError):
    def __init__(self) -> None:
        super().__init__("canvas_auth_route_untrusted")


def _normalized_https_origin(url: str) -> tuple[str, str]:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise CanvasTrustError() from error
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise CanvasTrustError()
    return f"https://{host}", host


def _is_canvas_host(host: str) -> bool:
    return host == _CANVAS_HOST_SUFFIX or host.endswith(f".{_CANVAS_HOST_SUFFIX}")


class _CanvasAuthRoute:
    def __init__(self, entry_url: str) -> None:
        self.entry_origin, entry_host = _normalized_https_origin(entry_url)
        if entry_host != _CANVAS_ENTRY_HOST:
            raise CanvasTrustError()
        self.reset()

    def reset(self) -> None:
        self._phase = "entry"

    def observe(self, url: str) -> None:
        _, host = _normalized_https_origin(url)
        if self._phase == "entry":
            if _is_canvas_host(host):
                return
            if host == _CANVAS_TRANSIT_HOST:
                self._phase = "transit"
                return
        elif self._phase == "transit":
            if host == _CANVAS_TRANSIT_HOST:
                return
            if host == _MICROSOFT_LOGIN_HOST:
                self._phase = "microsoft"
                return
        elif self._phase == "microsoft":
            if host == _MICROSOFT_LOGIN_HOST:
                return
            if _is_canvas_host(host):
                self._phase = "canvas_return"
                return
        elif self._phase == "canvas_return" and _is_canvas_host(host):
            return
        raise CanvasTrustError()

    def require_microsoft_credentials(self) -> None:
        if self._phase != "microsoft":
            raise CanvasTrustError()

    def verified_canvas_origin(self, url: str) -> str:
        origin, host = _normalized_https_origin(url)
        self.observe(url)
        if self._phase != "canvas_return" or not _is_canvas_host(host):
            raise CanvasTrustError()
        return origin

def _origin(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else url


def _term_context_from_today() -> TermContext:
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


def _build_term_regexes(fall_year: int, spring_year: int, term: str) -> tuple[List[re.Pattern[str]], List[re.Pattern[str]]]:
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


def _matches_current_term(text: str, allow: List[re.Pattern[str]], deny: List[re.Pattern[str]]) -> bool:
    t = text or ""
    if any(r.search(t) for r in deny):
        return False
    return any(r.search(t) for r in allow)


# --------------------- engine ---------------------

class CanvasEngine(PortalEngine):
    """
    Expects runner to pass:
      - username (Student.P1Username)
      - password (Student.P1Password)
      - login_url (Student.Portal1), e.g. https://<tenant>.instructure.com/login/canvas
    """

    portal_key = "canvas"
    url_patterns = ("instructure.com", "canvas")
    agenda_capable = True

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
        try:
            _, host = _normalized_https_origin(self.page.url or "")
        except CanvasTrustError:
            return False
        if not _is_canvas_host(host):
            return False

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

        return False

    async def _has_canvas_login_error(self) -> bool:
        error_targets = [
            self.page.get_by_role("alert"),
            self.page.locator(".ic-Login-error"),
            self.page.locator(".alert"),
            self.page.locator(".error"),
            self.page.locator(".ic-flash-error"),
            self.page.locator("text=/invalid|incorrect|failed|unsuccessful|try again/i"),
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
            self._raise_canvas_route_error()
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=800)
            except Exception:
                pass
            self._raise_canvas_route_error()

            if await self._is_canvas_logged_in():
                self._raise_canvas_route_error()
                return True

            if await self._has_canvas_login_error():
                self._raise_canvas_route_error()
                return False

            await self.page.wait_for_timeout(500)

        raise PlaywrightTimeout("Timed out waiting for Canvas login result")

    async def _click_sso_entry_if_needed(self):
        """
        Some Canvas pages first show SSO buttons rather than credential fields.
        """
        sso_selectors = (
            "button:has-text('Log In With Microsoft')",
            "a:has-text('Log In With Microsoft')",
            "button:has-text('Sign in with Microsoft')",
            "a:has-text('Sign in with Microsoft')",
            "button:has-text('Microsoft')",
            "a:has-text('Microsoft')",
            "button:has-text('Single Sign-On')",
            "a:has-text('Single Sign-On')",
            "button:has-text('SSO')",
            "a:has-text('SSO')",
        )

        for sso_sel in sso_selectors:
            try:
                loc = self.page.locator(sso_sel).first
                if await exists(loc, timeout=800):
                    await self._run_canvas_auth_action(loc.click())
                    await self._run_canvas_auth_action(
                        wait_after_nav(self.page, wait_until="domcontentloaded")
                    )
                    return
            except CanvasTrustError:
                raise
            except Exception:
                continue

    # ----------------- login -----------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.8, min=0.8, max=3),
        retry=retry_if_exception_type(PlaywrightTimeout),
        reraise=True,
    )
    async def _prepare_login(self, route: _CanvasAuthRoute) -> None:
        route.reset()
        await self._run_canvas_auth_action(
            self.page.goto(self.login_url, wait_until="domcontentloaded")
        )
        self._raise_canvas_route_error()
        route.observe(self.page.url)
        await self.page.wait_for_timeout(750)
        await self._click_sso_entry_if_needed()
        self._raise_canvas_route_error()

        try:
            _, host = _normalized_https_origin(self.page.url)
        except CanvasTrustError:
            raise
        if host != _MICROSOFT_LOGIN_HOST:
            await self._run_canvas_auth_action(
                self.page.wait_for_url(
                    lambda url: _normalized_https_origin(url)[1]
                    == _MICROSOFT_LOGIN_HOST,
                    timeout=15_000,
                    wait_until="domcontentloaded",
                )
            )
        self._raise_canvas_route_error()
        route.observe(self.page.url)
        route.require_microsoft_credentials()

    def _install_canvas_route_guard(self, route: _CanvasAuthRoute) -> None:
        self._canvas_route_error: CanvasTrustError | None = None

        def observe_main_frame(frame: Any) -> None:
            if frame is not self.page.main_frame:
                return
            try:
                route.observe(frame.url)
            except CanvasTrustError as error:
                self._canvas_route_error = error

        self._canvas_route_callback: Callable[[Any], None] = observe_main_frame
        self.page.on("framenavigated", observe_main_frame)

    def _remove_canvas_route_guard(self) -> None:
        callback = getattr(self, "_canvas_route_callback", None)
        if callback is not None:
            self.page.off("framenavigated", callback)
            del self._canvas_route_callback

    def _raise_canvas_route_error(self) -> None:
        error = getattr(self, "_canvas_route_error", None)
        if error is not None:
            raise error

    async def _run_canvas_auth_action(self, action: Awaitable[Any]) -> Any:
        try:
            result = await action
        except Exception:
            self._raise_canvas_route_error()
            raise
        self._raise_canvas_route_error()
        return result

    async def _submit_microsoft_credentials_once(self, route: _CanvasAuthRoute) -> None:
        self._raise_canvas_route_error()
        route.observe(self.page.url)
        route.require_microsoft_credentials()
        try:
            await self.page.fill("input#username", self.sid, timeout=1000)
        except PlaywrightTimeout:
            self._raise_canvas_route_error()
            route.observe(self.page.url)
            route.require_microsoft_credentials()
            await self.page.fill("input#i0116", self.sid, timeout=1000)
            self._raise_canvas_route_error()
            route.observe(self.page.url)
            route.require_microsoft_credentials()
            await self._run_canvas_auth_action(self.page.click("#idSIButton9"))
            route.observe(self.page.url)
            route.require_microsoft_credentials()
            await self.page.fill("input#i0118", self.pw)
            self._raise_canvas_route_error()
            await self._run_canvas_auth_action(self.page.click("#idSIButton9"))
        else:
            self._raise_canvas_route_error()
            route.observe(self.page.url)
            route.require_microsoft_credentials()
            await self.page.fill("input#password", self.pw)
            self._raise_canvas_route_error()
            await self._run_canvas_auth_action(
                self.page.locator('.form-group input[name="password"]').press("Enter")
            )

        await self._run_canvas_auth_action(
            self.page.wait_for_load_state("domcontentloaded")
        )

        stay_signed_in = self.page.get_by_text("Stay signed in?")
        if await stay_signed_in.count() > 0:
            route.observe(self.page.url)
            route.require_microsoft_credentials()
            await self._run_canvas_auth_action(self.page.click("#idSIButton9"))

    async def login(self, first_name: Optional[str] = None):
        """
        Fill creds, submit, and land in a valid post-login Canvas state.
        """
        _ = first_name
        self.__dict__.pop("_canvas_origin", None)
        if not self.login_url:
            raise LoginError("portal login rejected")

        route = _CanvasAuthRoute(self.login_url)
        self._install_canvas_route_guard(route)
        try:
            await self._prepare_login(route)
            await self._submit_microsoft_credentials_once(route)

            login_ok = await self._wait_for_login_result(timeout_ms=14000)
            await self.raise_login_error_if(not login_ok)

            await self.post_login()

            ok = await self._is_canvas_logged_in()
            await self.raise_login_error_if(not ok)
            self._raise_canvas_route_error()
            self._canvas_origin = route.verified_canvas_origin(self.page.url)
        except CanvasTrustError:
            raise
        except PlaywrightTimeout:
            raise
        except LoginError:
            raise LoginError("portal login rejected") from None
        except Exception:
            raise LoginError("portal login rejected") from None
        finally:
            self._remove_canvas_route_guard()

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

    # async def post_login(self):
    #     # handle student view popup
    #     student_tour = await exists(self.page.get_by_text('Student Tour'))
    #     if student_tour:
    #         not_now_button = self.page.get_by_role('button', name='Not Now')
    #         await not_now_button.click()
    #         done_button = self.page.get_by_role('button', name='Done')
    #         if await exists(done_button):
    #             await done_button.click()

    #     # ensure we are on list view
    #     show_grades_button = self.page.locator('[data-testid="show-my-grades-button"]')
    #     if await show_grades_button.count() == 0: # no show grades button, switch to list view
    #         await self.page.locator('[data-testid="dashboard-options-button"]').click()
    #         await self.page.locator('[data-testid="list-view-menu-item"]').click()

    #         await self.page.wait_for_selector('[data-testid="show-my-grades-button"]')
    #         await self.page.wait_for_timeout(1500)

    # ----------------- grades scraping -----------------

    async def fetch_grades(self) -> GradeMap:
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
            self.logger.info(
                "portal.fetch.completed", extra={"course_count": len(parsed)}
            )
            return parsed
        except Exception as e:
            self.logger.error(
                "portal.fetch.failed", extra={"exception_type": type(e).__name__}
            )
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
                return parsed # {}

            await self.page.wait_for_selector('[data-testid="my-grades-score"]', state='attached')
            # 2. parse
            course_grades = await self.page.locator('[data-testid="my-grades-score"]').all()
            count = len(course_grades)
            self.logger.debug(
                "portal.fetch.grade_cards_found", extra={"course_count": count}
            )

            grade_str: str | None = None
            for i in range(count):
                course_grade = course_grades[i]
                course_card = course_grade.locator('xpath=..') # nav to the parent, we got a list of grades which are inner elems
                course = await course_card.get_by_role('link').inner_text()
                grade_str = await course_grade.inner_text()
                if grade_str.lower() == "no grade":
                    continue
                grade = canonicalize_grade(grade_str)
                if grade is not None:
                    parsed[canonicalize_course_title(course)] = grade
            return parsed

        course_grades = await self.page.locator('[data-testid="my-grades-score"]').all()
        count = len(course_grades)
        self.logger.debug(
            "portal.fetch.grade_cards_found", extra={"course_count": count}
        )

        for i in range(count):
            course_grade = course_grades[i]
            course_card = course_grade.locator("xpath=..")
            course = await course_card.get_by_role("link").inner_text()
            grade_str = await course_grade.inner_text()
            if grade_str.lower() == "no grade":
                continue
            grade = canonicalize_grade(grade_str)
            if grade is not None:
                parsed[canonicalize_course_title(course)] = grade

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
                        results[canonicalize_course_title(course_name)] = float(
                            course_result["final_percent"]
                        )
                    except Exception:
                        pass

            except TimeoutError:
                self.logger.warning("portal.fetch.course_timeout")
            except Exception as e:
                self.logger.warning(
                    "portal.fetch.course_failed",
                    extra={"exception_type": type(e).__name__},
                )

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

    async def get_agenda(self) -> list[AgendaRecord]:
        canvas_origin = getattr(self, "_canvas_origin", None)
        if not isinstance(canvas_origin, str):
            from .canvas_agenda import CanvasAgendaError

            raise CanvasAgendaError("canvas_agenda_origin_unverified")
        return await collect_canvas_agenda(self.page, canvas_origin)
