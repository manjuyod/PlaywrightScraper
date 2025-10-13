from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple
import re
from datetime import datetime, timedelta  # ← added timedelta
from bs4 import BeautifulSoup
from . import register_portal 
from .base import PortalEngine
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from scraper.portals.infinite_campus import InfiniteCampus
# TODO: Uses Infinite Campus after RapidIdentity; you can remove those pieces later if not needed.
@register_portal("gps")
class GPS(PortalEngine):
    """Portal scraper for Gilbert Public Schools' portal.

    The class uses Playwright to automate login and extract quarter grades
    for each course. Grades are returned as a list of course/grade
    dictionaries under the ``parsed_grades`` key.
    """

    LOGIN = "https://gpsportal.gilberted.net/"
    HOME_WRAPPER = (
        "https://gilbertaz.infinitecampus.org/campus/nav-wrapper/student/portal/student/home?appName=gilbert"
    )
    LOGOFF = (
        "https://gilbertaz.infinitecampus.org/campus/portal/student/gilbert.jsp?status=logoff"
    )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(TimeoutError),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the user on the GPS parent portal."""
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")

        # Username
        await self.page.fill("input#identification", self.sid)
        await self.page.wait_for_timeout(200)
        await self.page.click("button#authn-go-button")

        # Password
        await self.page.fill("input#ember534", self.pw)
        await self.page.wait_for_timeout(2000)
        await self.page.locator("#authn-go-button").evaluate("btn => btn.click()")

        # RapidIdentity often doesn't change URL. Wait for pictograph tiles instead.
        await self.page.locator(".pictograph-list img.tile-icon").first.wait_for(
            state="visible", timeout=15_000
        )
        await self.page.wait_for_load_state("networkidle")
        print("trying pictograph\n")

        # Pictograph auth (three picks)
        for _ in range(0, 3):
            images_alts = await self.page.eval_on_selector_all(
                ".pictograph-list img.tile-icon", "imgs => imgs.map(img => img.alt)"
            )
            print("Alt tags: ", images_alts)
            print("Auth: ", self.auth_images)
            assert self.auth_images is not None  # must be provided by caller/DB
            user_match = next((image for image in self.auth_images if image in images_alts), None)
            if not user_match:
                raise RuntimeError(f"No pictograph match found in {images_alts} for {self.auth_images}")
            await self.page.locator(
                f".pictograph-list img.tile-icon[alt='{user_match}']"
            ).click()
            await self.page.wait_for_timeout(1000)
        # nav to infinite campus portal
        async with self.page.expect_popup(timeout=0) as popup:
            await self.page.locator("img[alt='STUDENT INFINITE CAMPUS']").click()
            self.page = await popup.value
            await self.page.wait_for_load_state()
            # await self.page.wait_for_selector()
        await self.page.wait_for_load_state('networkidle')
        await self.page.wait_for_timeout(1000)

        await self.raise_if_login_error('nav-wrapper' not in self.page.url)

        print("Successfully reached the home page")
        await self.page.wait_for_load_state(timeout=10000)
        await self.page.wait_for_timeout(1500)
        await self.page.context.tracing.stop()

    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(TimeoutError),
    )
    async def fetch_grades(self) -> Dict[str, Any] | None:
        """Collect grades from the grade tab"""
        # GPS uses Infinite Campus as their portal, GPS is just a login wrapper
        try:
            return await InfiniteCampus(self.page, self.sid, self.pw, self.login_url).fetch_grades()
        finally:
            # await self.page.pause()
            pass
            # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
