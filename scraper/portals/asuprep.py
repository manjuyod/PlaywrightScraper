from __future__ import annotations
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup
import re
from scraper.portals.base import PortalEngine, PlaywrightTimeout
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .utils import *
@register_portal("asuprep")
class ASUPrep(PortalEngine):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try:
            # sso_login_selector = 'a[id="Google;btn"]'
            username_selector = "#ctl00_centerLoginContent_tbLogin"
            password_selector = "#ctl00_centerLoginContent_tbPassword"
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                username_selector=username_selector,
                password_selector=password_selector,
            )
            await wait_after_nav(self.page, wait_until='networkidle', wait_after_load=10000)
            
            # nav to gradebook
            await self.page.click('a:has-text("Gradebook")')
            await wait_after_nav(self.page, wait_until='networkidle', wait_after_load=5000)
        except Exception as e:
            print(type(e), e)
            raise

    async def alt_sso_login(self):
        await self.microsoft_login()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        try:
            
            table_selector = ""
            title_selector = ""
            grade_selector = ""
            
            await self.page.pause()

            return await grades_table_to_dict(
                self.page,
                table_selector,
                title_selector,
                grade_selector,
            )
        except Exception as e:
            print(type(e), e)
            return {}