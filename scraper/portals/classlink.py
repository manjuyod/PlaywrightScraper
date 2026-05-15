from __future__ import annotations
from typing import Any, Dict, Optional
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from .base import PortalEngine, PlaywrightTimeout
from . import register_portal
from scraper.portals.infinite_campus import InfiniteCampus
from .utils import universal_login_flow, wait_after_nav

# TODO: Uses Infinite Campus after RapidIdentity; you can remove those pieces later if not needed.
@register_portal("classlink")
class Classlink(PortalEngine):
    """Classlink is purely a passthrough to other portals, but must be used sometimes as SSO"""
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try:
            username_selector = 'input#username'
            pw_selector = 'input#password'
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                username_selector,
                pw_selector
            )
            await wait_after_nav(self.page, pattern='https://myapps.classlink.com/home')

            assert self.alt_portal_url is not None, "Classlink scraper requires an alt_portal_url to navigate to after login"
            await self.page.goto(url=self.alt_portal_url, wait_until="domcontentloaded")
            if 'infinitecampus' in self.alt_portal_url:
                # nav to infinite campus portal
                async with self.page.expect_navigation(url='**/nav-wrapper/student/portal/student/**', wait_until="domcontentloaded", timeout=0) as _:
                    await self.page.locator('#samlLoginLink').click()

        except Exception as e:
            print(e)
            raise self.LoginError(e)
        finally:
            # await self.page.wait_for_load_state("networkidle")
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
        else:
            return {}
    # ---------------------- PARSER ------------------------------------------
