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

    async def validate_login(self) -> None:
        config = type(self).login_config
        assert config is not None
        rejected = await self.page.locator(config.username_selector).is_visible()
        if not rejected:
            rejected = await self.page.locator(config.password_selector).is_visible()
        await self.raise_login_error_if(rejected)

    async def after_login(self, first_name: str | None) -> None:
        _ = first_name
        try:
            await self.do_gps_auth()
        except PlaywrightTimeout:
            raise self.LoginError("portal login rejected") from None

    # Login Helper
    async def do_gps_auth(self):
        assert self.auth_images is not None  # must be provided by caller/DB

        await self.page.locator(".pictograph-list img.tile-icon").first.wait_for(
            state="visible", timeout=15_000
        )
        await self.page.wait_for_load_state("networkidle")

        for _ in range(0, 3):
            images_alts = await self.page.eval_on_selector_all(
                ".pictograph-list img.tile-icon", "imgs => imgs.map(img => img.alt)"
            )
            
            user_match = None
            for alt in images_alts:
                if alt in self.auth_images:
                    user_match = alt
                    break
            if not user_match:
                raise RuntimeError("pictograph authentication failed")
            await self.page.locator(
                f".pictograph-list img.tile-icon[alt='{user_match}']"
            ).click()
            await self.page.wait_for_timeout(1000)

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
        return await engine(
            self.page, self.sid, self.pw, self.login_url
        ).fetch_grades()
