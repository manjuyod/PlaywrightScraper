from __future__ import annotations
from typing import Any, Dict, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from bs4 import BeautifulSoup

from scraper.portals.base import PortalEngine, PlaywrightTimeout
from scraper.portals import register_portal, get_portal
from .utils import *

@register_portal("google_classroom")
class GoogleClassroom(PortalEngine):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try: # theoretically should just use the Google sign in
            # in reality, after inserting the username the page may reroute to some internal portal
            if self.login_url != self.page.url:  # Only nav if we are not at the target page
                await self.page.goto(self.login_url, wait_until="domcontentloaded")
            try:
                await self.google_login()
            except PlaywrightTimeout:
                portal = get_portal_key_from_url(self.page.url)
                if portal != 'google_classroom': # new portal reached, create a new engine and login there
                    Engine = get_portal(portal)
                    scraper = Engine(
                        self.page,
                        self.sid,
                        self.pw,
                        login_url=self.page.url,  # use the alternate as this should be where class info resides
                        student_name=self.student_name,
                        auth_images=self.auth_images
                    )
                    await scraper.login()
        except Exception as e:
            print(f"{type(e)}: {e}")
            raise
        finally:
            await self.page.context.tracing.stop()

    async def get_agenda(self):
        await self.page.pause()

    async def fetch_grades(self) -> Dict[str, Any]:
        try:
            table_selector = 'None'
            title_selector = 'None'
            pair_selector = 'None'
            grade_selector = 'None'
            return await grades_table_to_dict(
                self.page,
                table_selector,
                title_selector,
                grade_selector,
                pair_selector=pair_selector,
                should_truncate_before=True
            )
        except Exception as e:
            print(f"{type(e)}: {e}")
            raise
        finally:
            pass

    async def logout(self) -> None:
        await self.page.wait_for_timeout(300)
