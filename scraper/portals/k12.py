from __future__ import annotations
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup
import re
from scraper.portals.base import PortalEngine, PlaywrightTimeout
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from scraper.portals.utils import universal_login_flow


@register_portal("k12")
class K12(PortalEngine):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try:
            # # 1) Load login page
            # await self.page.goto(self.login_url, wait_until="domcontentloaded")
            # await self.page.wait_for_timeout(500)
            # # Fill username
            # await self.page.fill('#okta-signin-username', self.sid)
            # # Fill password
            # await self.page.fill('#okta-signin-password', self.pw)
            # # Login
            # # with self.page.expect_navigation()
            # await self.page.locator('#okta-signin-submit').click()
            # await self.page.wait_for_timeout(3000)
            username_selector = '#okta-signin-username'
            pw_selector = '#okta-signin-password'
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                username_selector,
                pw_selector
            )
            # nav to grades page
        except Exception as e:
            print(e)
            raise
        finally:
            await self.page.pause()
            pass
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        if 'grades' not in self.page.url:
            raise self.LoginError('No grades page')
        parsed = {}
        try:
            pass
        except Exception as e:
            print(e)
        finally:
            print(parsed)
            # await self.page.pause()
            return parsed
