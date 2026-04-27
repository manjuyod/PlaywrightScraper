from __future__ import annotations

import re
from datetime import datetime, timedelta  # ← added timedelta
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scraper.portals.infinite_campus import InfiniteCampus

from . import register_portal
from .base import PortalEngine
from .utils import *


@register_portal("gps")
class GPS(PortalEngine):
    """Portal scraper for Gilbert Public Schools' portal.

    The class uses Playwright to automate login and extract quarter grades
    for each course. Grades are returned as a list of course/grade
    dictionaries under the ``parsed_grades`` key.
    """

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the user on the GPS parent portal."""
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        username_selector = "input#identification"
        password_selector = "input#ember535"
        await universal_login_flow(
            self.page,
            self.login_url,
            self.sid,
            self.pw,
            username_selector,
            password_selector,
            post_fill_wait=4000,
        )

        # Pictograph auth (three picks)
        print("waiting on pictograph\n")
        await self.do_gps_auth()

        await self.page.context.tracing.stop()

    # Login Helper
    async def do_gps_auth(self):
        print(self.auth_images)
        assert self.auth_images is not None  # must be provided by caller/DB

        await self.page.locator(".pictograph-list img.tile-icon").first.wait_for(
            state="visible", timeout=15_000
        )
        await self.page.wait_for_load_state("networkidle")

        for _ in range(0, 3):
            images_alts = await self.page.eval_on_selector_all(
                ".pictograph-list img.tile-icon", "imgs => imgs.map(img => img.alt)"
            )
            print("Page images: ", images_alts)
            print("Auth images: ", self.auth_images)
            user_match = next(
                (image for image in self.auth_images if image in images_alts), None
            )
            if not user_match:
                raise RuntimeError(
                    f"No pictograph match found in {images_alts} for {self.auth_images}"
                )
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
        print("Successfully reached the home page")

    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(TimeoutError),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """Collect grades from the grade tab"""
        # GPS uses Infinite Campus as their portal, GPS is just a login wrapper
        try:
            await self.nav_to_ic()
            return await InfiniteCampus(
                self.page, self.sid, self.pw, self.login_url
            ).fetch_grades()
        finally:
            pass
