from __future__ import annotations

import re
from datetime import datetime, timedelta  # ← added timedelta
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)
from .base import PortalEngine
from . import register_portal


# TODO: Uses Infinite Campus after RapidIdentity; you can remove those pieces later if not needed.
@register_portal("classlink_newport")
class ClasslinkNewport(PortalEngine):
    """Portal scraper for Classlink portal.

    The class uses Playwright to automate login and extract quarter grades
    for each course. Grades are returned as a list of course/grade
    dictionaries under the ``parsed_grades`` key.
    """

    LOGIN = "https://launchpad.classlink.com/nmusd"
    HOME_WRAPPER = (
    )
    LOGOFF = (
    )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the user on the GPS parent portal."""
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")

        # Username
        await self.page.fill("input#username", self.sid)
        await self.page.wait_for_timeout(2000)
        # Password
        await self.page.fill("input#password", self.pw)
        await self.page.wait_for_timeout(2000)
        
        # sign-in button
        await self.page.locator("#signin").click()
        await self.page.wait_for_load_state("networkidle")
        await self.page.context.tracing.stop()
    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        pass

    # ---------------------- PARSER ------------------------------------------
    

    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
