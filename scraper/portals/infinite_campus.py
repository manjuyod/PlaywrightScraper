from __future__ import annotations
from datetime import datetime
from typing import ClassVar

from scraper.agenda_contract import AgendaRecord
from playwright.async_api import Frame, Page, TimeoutError as PlaywrightTimeout, expect
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from typing_extensions import override

from .infinite_campus_agenda import collect_infinite_campus_agenda
from .base import GradeMap, PortalEngine, UniversalLoginConfig
from .utils import exists, grades_table_to_dict


class InfiniteCampus(PortalEngine):
    """Portal scraper for Infinite Campus."""

    portal_key: ClassVar[str] = "infinite_campus"
    url_patterns: ClassVar[tuple[str, ...]] = ("campus/portal", "infinitecampus")
    agenda_capable: ClassVar[bool] = True
    login_config: ClassVar[UniversalLoginConfig | None] = UniversalLoginConfig(
        username_selector="#username",
        password_selector="#password",
        microsoft_sso=True,
        google_sso=True,
        pre_fill_wait=5000,
    )
    _GRADE_CARDS: ClassVar[str] = "div.collapsible-card.grades__card"
    _TIMEFRAME_SETTLE_MS: ClassVar[int] = 750

    @override
    async def validate_login(self) -> None:
        invalid = await exists(
            self.page.get_by_text("Incorrect Username and/or Password", exact=False)
        )
        await self.raise_login_error_if(invalid or "nav-wrapper" not in self.page.url)

    @override
    async def after_login(self, first_name: str | None) -> None:
        await self.page.wait_for_load_state("networkidle")
        await self.select_student(first_name, self.page)
        self.logger.debug("portal.login.student_home_ready")

    # helper
    async def select_student(self, first_name: str | None, page: Page) -> None:
        parent = page.frame("main-workspace")
        if not parent:
            parent = page
        if not first_name:
            return
        try:  # click the student with first name if it exists
            self.logger.debug("portal.student_selection.started")
            await parent.get_by_role("link", name=first_name, exact=False).click(
                timeout=2000
            )
        except PlaywrightTimeout:
            self.logger.info("portal.student_selection.not_available")
            pass  # no alternate student

    # ---------------------- NAV TO GRADES -------
    async def nav_to_grades(self, *, force: bool = False) -> None:
        grades_url_pattern = "**/grades*"
        menu_selector = "#menu-toggle-button"
        grades_button_label = "Grades"
        on_grades_page = False
        if not force:
            try:  # are we already on the page?
                await expect(self.page).to_have_url(grades_url_pattern)
                on_grades_page = True
            except AssertionError:
                pass
        if not on_grades_page:
            _ = await self.page.wait_for_selector(menu_selector)
            await self.page.locator(menu_selector).click()
            await self.page.get_by_role("link", name=grades_button_label).click()
            await self.page.wait_for_url(grades_url_pattern, timeout=20000)
            await self.page.wait_for_load_state("networkidle")

    @staticmethod
    def term_semester_from_today() -> int:
        """
        Determine current academic term semester.

        Return: 1 for Fall, 2 for Spring
        """
        now = datetime.now()
        m = now.month
        if m >= 8:  # Aug–Dec → Fall of current year
            sem = 1
        elif m <= 5:  # Jan–May → Spring of previous fall year
            sem = 2
        else:  # Jun–Jul → prep for upcoming Fall
            sem = 1
        return sem

    # ---------------------- FETCH (notifications → latest per subject) -------
    @override
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(
        self,
    ) -> GradeMap:  # TODO: Alter to parse from 'All terms' instead of 'Current term'
        """Collect grades from the grade tab"""
        await self.page.wait_for_load_state()
        await self.page.wait_for_timeout(1500)
        # get grades
        try:
            # 0) ensure we are on the grades page and targeting the right timeframe
            await self.nav_to_grades()

            frame_selector = "main-workspace"
            frame = self.page.frame(frame_selector)
            assert frame is not None, "Infinite Campus main workspace frame not found"
            # target the correct timeframe

            # collect grades
            table_selector = self._GRADE_CARDS
            course_selector = "h4 a"
            grades_selector = ".grading-score div"

            quarter_groups, semester_group = self.timeframe_groups(
                self.term_semester_from_today()
            )
            snapshots: list[GradeMap] = []
            for names in quarter_groups:
                if await self.select_timeframe(frame, names) is None:
                    continue
                snapshots.append(
                    await grades_table_to_dict(
                        self.page,
                        table_selector,
                        course_selector,
                        grades_selector,
                        frame_selector=frame_selector,
                        use_soup=False,
                    )
                )

            if not snapshots:
                if await self.select_timeframe(frame, semester_group) is None:
                    raise AssertionError("Infinite Campus timeframe not found")
                snapshots.append(
                    await grades_table_to_dict(
                        self.page,
                        table_selector,
                        course_selector,
                        grades_selector,
                        frame_selector=frame_selector,
                        use_soup=False,
                    )
                )

            merged_grades = self.merge_grade_snapshots(snapshots)

            self.logger.info(
                "portal.fetch.completed", extra={"course_count": len(merged_grades)}
            )
            return merged_grades
        except Exception as exc:
            self.logger.error(
                "portal.fetch.failed", extra={"exception_type": type(exc).__name__}
            )
            raise
        finally:
            self.logger.debug("portal.fetch.finished")

    @staticmethod
    def timeframe_groups(
        semester: int,
    ) -> tuple[tuple[tuple[str, ...], tuple[str, ...]], tuple[str, ...]]:
        first_quarter = semester * 2 - 1
        second_quarter = semester * 2
        return (
            (
                (f"QT{first_quarter}", f"Q{first_quarter}"),
                (f"QT{second_quarter}", f"Q{second_quarter}"),
            ),
            (f"S{semester}",),
        )

    @staticmethod
    def merge_grade_snapshots(snapshots: list[GradeMap]) -> GradeMap:
        merged: GradeMap = {}
        for snapshot in snapshots:
            merged.update(snapshot)
        return merged

    @classmethod
    async def select_timeframe(
        cls,
        frame: Frame,
        names: tuple[str, ...],
    ) -> str | None:
        for name in names:
            timeframe = frame.get_by_role("button", name=name, exact=True)
            if not await exists(timeframe):
                continue
            await timeframe.click()
            await frame.wait_for_timeout(cls._TIMEFRAME_SETTLE_MS)
            return name
        return None

    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        # await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)

    @override
    async def get_agenda(self) -> list[AgendaRecord]:
        await self.nav_to_grades()

        async def return_to_grades() -> None:
            await self.nav_to_grades(force=True)

        frame = self.page.frame("main-workspace")
        assert frame is not None, "Infinite Campus main workspace frame not found"
        quarter_groups, semester_group = self.timeframe_groups(
            self.term_semester_from_today()
        )
        records: list[AgendaRecord] = []
        selected_quarter = False
        for names in quarter_groups:
            if await self.select_timeframe(frame, names) is None:
                continue
            selected_quarter = True
            if await frame.locator(f"{self._GRADE_CARDS}:visible").count() > 0:
                records.extend(
                    await collect_infinite_campus_agenda(
                        self.page,
                        return_to_grades=return_to_grades,
                    )
                )
                await return_to_grades()
                frame = self.page.frame("main-workspace")
                assert frame is not None

        if selected_quarter:
            return records

        if await self.select_timeframe(frame, semester_group) is None:
            raise AssertionError("Infinite Campus timeframe not found")
        return await collect_infinite_campus_agenda(
            self.page,
            return_to_grades=return_to_grades,
        )
