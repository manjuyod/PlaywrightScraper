from __future__ import annotations

import re
from datetime import datetime, timedelta  # ← added timedelta
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)
from .base import PortalEngine
from . import register_portal
from scraper.portals.infinite_campus import InfiniteCampus

# TODO: Uses Infinite Campus after RapidIdentity; you can remove those pieces later if not needed.
@register_portal("classlink")
class Classlink(PortalEngine):
    """Classlink is purely a passthrough to other portals, but must be used sometimes as SSO"""
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try:
            await self.page.context.tracing.start(screenshots=True, snapshots=True)
            await self.page.goto(self.login_url, wait_until="domcontentloaded")

            # Username
            await self.page.fill("input#username", self.sid)
            await self.page.wait_for_timeout(2000)
            # Password
            await self.page.fill("input#password", self.pw)
            await self.page.wait_for_timeout(2000)

            # sign-in button
            await self.page.get_by_role("button", name="Sign In").click()
            await self.page.wait_for_url('https://myapps.classlink.com/home')

            await self.page.goto(self.alt_portal_url, wait_until="domcontentloaded")
            if 'infinitecampus' in self.alt_portal_url:
                # nav to infinite campus portal
                async with self.page.expect_navigation(url='**/nav-wrapper/student/portal/student/**', wait_until="domcontentloaded", timeout=0) as popup:
                    await self.page.locator('#samlLoginLink').click()

        except Exception as e:
            print(e)
            raise
        finally:
            # await self.page.wait_for_load_state("networkidle")
            print('completed login')
            await self.page.context.tracing.stop()
    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        print('fetching grades')
        if 'infinitecampus' in self.page.url:
            return await InfiniteCampus(self.page, self.sid, self.pw, self.login_url).fetch_grades()
        else: return {}
    # ---------------------- PARSER ------------------------------------------


