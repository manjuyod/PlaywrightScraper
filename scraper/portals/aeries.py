from __future__ import annotations

from time import monotonic

from bs4 import Tag

from .base import GradeMap, PortalEngine, UniversalLoginConfig
from .utils import exists, wait_after_nav, universal_login_flow, grades_table_to_dict, canonicalize_course_title, canonicalize_grade, PlaywrightTimeout
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


class Aeries(PortalEngine):
    portal_key = "aeries"
    url_patterns = ("aeries", "LoginParent.aspx", "Dashboard.aspx")
    login_config = UniversalLoginConfig(
        username_selector="input#portalAccountUsername",
        password_selector="input#portalAccountPassword",
        sso_entry_selector="#LoginButton",
        google_sso=True,
        alternate_sso=True,
    )

    async def _is_logged_in(self) -> bool:
        url = self.page.url or ""

        success_selectors = [
            "#StudentNameDropDown",
            "#NavMainGrades",
            "#divClass",
            "a[href*='Dashboard']",
            "a[href*='Grades']",
        ]

        for sel in success_selectors:
            try:
                if await exists(self.page.locator(sel), timeout=700):
                    return True
            except Exception:
                pass

        return any(x in url.lower() for x in ("dashboard", "grades", "student"))

    async def _has_login_error(self) -> bool:
        error_targets = [
            self.page.get_by_role("alert"),
            self.page.locator(".alert"),
            self.page.locator(".validation-summary-errors"),
            self.page.locator("#divError"),
            self.page.locator("text=/invalid|incorrect|failed|try again|username|password/i"),
        ]

        for target in error_targets:
            try:
                if await exists(target, timeout=700):
                    return True
            except Exception:
                pass

        return False

    async def _wait_for_login_result(self, timeout_ms: int = 12000) -> bool:
        deadline = monotonic() + (timeout_ms / 1000)

        while monotonic() < deadline:
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=800)
            except Exception:
                pass

            if await self._is_logged_in():
                return True

            if await self._has_login_error():
                return False

            await self.page.wait_for_timeout(500)

        raise PlaywrightTimeout(f"Timed out waiting for Aeries login result (url={self.page.url})")

    async def validate_login(self) -> None:
        if not await self._wait_for_login_result(timeout_ms=14000):
            raise self.LoginError("portal login rejected")

    async def after_login(self, first_name: str | None) -> None:
        _ = first_name
        try:
            await wait_after_nav(
                self.page, pattern="**/Dashboard**", timeout=10000
            )
        except Exception:
            if not await self._is_logged_in():
                raise

    async def alternate_sso_login(self) -> None:
        username_selector = "#input28"
        pw_selector = "#input62"
        await universal_login_flow(
            self.page,
            self.page.url,
            self.sid,
            self.pw,
            username_selector,
            pw_selector,
        )

    async def nav_to_grades(self) -> bool:
        main_grades_selector = "#NavMainGrades"
        sub_grades_selector = "#NavSubGrades"
        await self.page.click(main_grades_selector)
        sub_grades_elem = self.page.locator(sub_grades_selector)
        if await exists(sub_grades_elem):
            await self.page.click(sub_grades_selector)
            await wait_after_nav(self.page, pattern="**/Grades**", timeout=5000)
            return True
        return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> GradeMap:
        self.logger.info("portal.fetch.started")
        try:
            await self.raise_login_error_if("Dashboard" not in self.page.url)
            await self.page.wait_for_timeout(3000)

            soup = await self.get_soup()
            class_table = soup.find("div", id="divClass")
            if class_table is None:
                await self.page.click("#StudentNameDropDown")
                await self.page.click("#StudentNameDropDownMenu")
                await self.page.wait_for_load_state()
                await self.page.wait_for_timeout(3000)

            grades_page_exists = await self.nav_to_grades()
            if grades_page_exists:
                table_selector = "tr[id$='ReadRow1']"
                course_selector = "td[data-tcfc='CRS.CO']"
                grade_selector = "td[data-tcfc='GRD.M2']"

                courses_dict = await grades_table_to_dict(
                    self.page,
                    table_selector,
                    course_selector,
                    grade_selector,
                    decompose_labels=True,
                )
                self.logger.info(
                    "portal.fetch.completed", extra={"course_count": len(courses_dict)}
                )
                return courses_dict
            else:
                self.logger.info("portal.fetch.dashboard_fallback")
                await self.page.reload()
                courses_dict = {}

                if isinstance(class_table, Tag) and len(class_table.select("div.Card")) > 0:
                    class_cards = class_table.select("div.Card")
                    self.logger.debug(
                        "portal.fetch.course_cards_found",
                        extra={"course_count": len(class_cards)},
                    )
                    for card in class_cards:
                        class_link = card.find("a", class_="TextHeading")
                        if class_link is None: 
                            continue
                        course_name: str = class_link.text.strip()

                        grade_div = card.find("div", class_="Grade")
                        if not isinstance(grade_div, Tag):
                            continue
                        grade_span = grade_div.find("span")
                        if grade_span is None:
                            continue
                        grade_str: str | None = grade_span.text.strip() if grade_span is not None else None
                        if not grade_str:
                            continue
                        title = canonicalize_course_title(course_name)
                        grade = canonicalize_grade(grade_str)
                        if grade is not None:
                            courses_dict[title] = grade

                self.logger.info(
                    "portal.fetch.completed", extra={"course_count": len(courses_dict)}
                )
                return courses_dict

        except Exception:
            raise

    async def logout(self) -> None:
        await self.page.wait_for_timeout(500)
