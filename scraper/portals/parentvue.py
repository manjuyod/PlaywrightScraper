from __future__ import annotations
from typing import Optional

from playwright.async_api import TimeoutError as PlaywrightTimeout
from scraper.portals.base import (
    GradeTableConfig,
    PortalEngine,
    UniversalLoginConfig,
)
from scraper.agenda_contract import AgendaRecord
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from .parentvue_course_scrub import collect_parentvue_course_agenda
from .utils import wait_after_nav

class ParentVUE(PortalEngine):
    portal_key = "parentvue"
    url_patterns = ("parentvue", "Login_Parent", "Login_Student")
    agenda_capable = True
    login_config = UniversalLoginConfig(
        username_selector="#ctl00_MainContent_username",
        password_selector="#ctl00_MainContent_password",
    )
    grade_table_config = GradeTableConfig(
        table_selector="div.gb-class-header.gb-class-row",
        title_selector=".course-title",
        grade_selector=".score",
        pair_selector="gb-class-row",
        should_truncate_before=True,
    )

    async def validate_login(self) -> None:
        await self.raise_login_error_if("Login" in self.page.url)

    async def after_login(self, first_name: str | None) -> None:
        try:
            await wait_after_nav(
                self.page, wait_until="domcontentloaded", timeout=30000
            )
            if "Login_Parent" in self.login_url:
                await self.select_student(first_name)
            self.logger.info("portal.navigation.gradebook_started")
            gradebook_link = self.page.locator(
                'a[href*="Gradebook"]:visible, a[href*="GradeBook"]:visible'
            ).first
            await gradebook_link.click()
            await self.page.wait_for_load_state(
                state="domcontentloaded", timeout=30000
            )
            await self.page.wait_for_selector("#gb-assignments", timeout=30000)
            await self.page.wait_for_selector(
                "#gb-assignments tr.gb-upcoming-assignment:visible, "
                "div.gb-class-header.gb-class-row:visible, "
                "#gb-assignments .no-data:visible",
                timeout=30000,
            )
        except PlaywrightTimeout:
            raise self.LoginError("portal login rejected") from None



    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def select_student(self, first_name: Optional[str] = None):
        """
        Open the student dropdown, click the matching student,
        grab its data-agu, then fetch and return the Gradebook HTML.
        """
        target = (first_name or getattr(self, "student_name", "") or "").strip()
        if not target:
            raise RuntimeError("No first_name provided to select_student()")
        self.logger.debug("portal.student_selection.started")
        target_lc = target.lower()

        # base selectors
        header_sel = "#ctl00_ctl00_MainContent_PXPHeader"
        selector_root = f"{header_sel} #ctl00_ctl00_MainContent_StudentSelector"
        current_button = f"{selector_root} .current"
        menu_ul = f"{selector_root} ul.dropdown-menu"
        item_info = f"{menu_ul} .student-info"
        current_name_sel = f"{selector_root} .current .student-name"

        # 1) If we're already on the right student, bail and grab its AGU
        try:
            name = (await self.page.locator(current_name_sel).inner_text()).strip()
            if target_lc in name.lower():
                # find its data-agu in the .current container
                agu = await self.page.locator(f"{selector_root} .current .student-info").get_attribute("data-agu")
                if not agu:
                    raise RuntimeError("Could not read data-agu from current student")
                self.logger.debug("portal.student_selection.already_selected")
                return
        except Exception:
            # continue to the dropdown approach
            pass

        # 2) Open the dropdown
        await self.page.click(current_button)
        await self.page.wait_for_selector(menu_ul, timeout=5000)

        # 3) Find and click the matching student-info
        items = self.page.locator(item_info)
        n = await items.count()
        if n == 0:
            raise RuntimeError("No student items in dropdown")

        agu = None
        for i in range(n):
            info = items.nth(i)
            name = (await info.locator(".student-name").inner_text()).strip()
            if target_lc in name.lower():
                agu = await info.get_attribute("data-agu")
                await info.click()
                self.logger.info("portal.student_selection.succeeded")
                break
        if not agu:
            raise RuntimeError(f"No dropdown student matched '{target}'")

    async def logout(self) -> None:
        await self.page.wait_for_timeout(300)

    async def get_agenda(self) -> list[AgendaRecord]:
        return await collect_parentvue_course_agenda(self.page)
