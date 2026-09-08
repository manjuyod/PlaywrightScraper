from __future__ import annotations
from playwright.async_api import TimeoutError as PlaywrightTimeout
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .base import GradeMap, PortalEngine, UniversalLoginConfig
from .utils import wait_after_nav

PICTOGRAPH_READINESS_TIMEOUT_MS = 45_000


class GPS(PortalEngine):
    """Portal scraper for Gilbert Public Schools' portal.

    The class uses Playwright to automate login and extract current grades
    for each course.
    """

    portal_key = "gps"
    url_patterns = ("gpsportal",)
    login_config = UniversalLoginConfig(
        username_selector="input#identification",
        password_selector="input#ember535",
        post_fill_wait=4000,
    )
    _AUTH_CONTAINER = ".pictograph-list"
    _AUTH_TILES = ".pictograph-list img.tile-icon"
    _AUTH_TIMEOUT_MS = PICTOGRAPH_READINESS_TIMEOUT_MS

    async def validate_login(self) -> None:
        await self._wait_for_auth_screen()

    async def _wait_for_auth_screen(self) -> None:
        starting_url = self.page.url
        self.logger.info("portal.login.auth_screen_waiting")
        try:
            await self.page.locator(self._AUTH_CONTAINER).wait_for(
                state="visible",
                timeout=self._AUTH_TIMEOUT_MS,
            )
            await self.page.locator(self._AUTH_TILES).first.wait_for(
                state="visible",
                timeout=self._AUTH_TIMEOUT_MS,
            )
        except PlaywrightTimeout:
            config = type(self).login_config
            assert config is not None
            try:
                login_form_visible = await self.page.locator(
                    config.username_selector
                ).is_visible() or await self.page.locator(
                    config.password_selector
                ).is_visible()
            except Exception:
                login_form_visible = False
            self.logger.warning(
                "portal.login.auth_screen_not_reached",
                extra={
                    "login_form_visible": login_form_visible,
                    "url_changed": self.page.url != starting_url,
                },
            )
            raise self.LoginError("portal login rejected") from None
        self.logger.info("portal.login.auth_screen_reached")

    async def after_login(self, first_name: str | None) -> None:
        _ = first_name
        try:
            await self.do_gps_auth()
        except PlaywrightTimeout:
            self.logger.warning("portal.login.auth_challenge_timed_out")
            raise self.LoginError("portal login rejected") from None

    # Login Helper
    async def do_gps_auth(self):
        if not self.auth_images:
            raise self.LoginError("portal login rejected")

        await self.page.locator(self._AUTH_TILES).first.wait_for(
            state="visible", timeout=PICTOGRAPH_READINESS_TIMEOUT_MS
        )

        for challenge in range(1, 4):
            images_alts = await self.page.eval_on_selector_all(
                self._AUTH_TILES,
                "imgs => imgs.map(img => img.alt)",
            )

            user_match = None
            for alt in images_alts:
                if alt in self.auth_images:
                    user_match = alt
                    break
            if not user_match:
                self.logger.warning(
                    "portal.login.auth_challenge_unmatched",
                    extra={
                        "challenge": challenge,
                        "tile_count": len(images_alts),
                        "configured_answer_count": len(self.auth_images),
                    },
                )
                raise self.LoginError("portal login rejected")
            await self.page.locator(
                f".pictograph-list img.tile-icon[alt='{user_match}']"
            ).click()
            await self.page.wait_for_timeout(1000)

        await self.page.locator(self._AUTH_CONTAINER).wait_for(
            state="hidden",
            timeout=self._AUTH_TIMEOUT_MS,
        )
        self.logger.info("portal.login.auth_completed")

    async def nav_to_ic(self):
        # nav to infinite campus portal
        async with self.page.expect_popup(timeout=0) as popup:
            await self.page.locator("img[alt='STUDENT INFINITE CAMPUS']").click()
            self.page = await popup.value

        await wait_after_nav(self.page, wait_after_load=5000, wait_until="networkidle")
        await self.raise_login_error_if("nav-wrapper" not in self.page.url)
        self.logger.info("portal.navigation.grades_home_reached")

    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(TimeoutError),
    )
    async def fetch_grades(self) -> GradeMap:
        """Collect grades from the grade tab"""
        # GPS uses Infinite Campus as their portal, GPS is just a login wrapper
        await self.nav_to_ic()
        from .registry import get_portal

        engine = get_portal("infinite_campus")
        return await engine(self.page, self.sid, self.pw, self.login_url).fetch_grades()
