from __future__ import annotations
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from .base import GradeMap, PortalEngine, UniversalLoginConfig
from .utils import wait_after_nav

# TODO: Uses Infinite Campus after RapidIdentity; you can remove those pieces later if not needed.
class Classlink(PortalEngine):
    """Classlink is purely a passthrough to other portals, but must be used sometimes as SSO"""
    portal_key = "classlink"
    url_patterns = ("classlink",)
    login_config = UniversalLoginConfig(
        username_selector="input#username",
        password_selector="input#password",
    )

    async def after_login(self, first_name: str | None) -> None:
        _ = first_name
        await wait_after_nav(
            self.page, pattern="https://myapps.classlink.com/home"
        )
        if self.alt_portal_url is None:
            raise self.LoginError("portal login rejected")
        await self.page.goto(url=self.alt_portal_url, wait_until="domcontentloaded")
        if "infinitecampus" in self.alt_portal_url:
            async with self.page.expect_navigation(
                url="**/nav-wrapper/student/portal/student/**",
                wait_until="domcontentloaded",
                timeout=0,
            ):
                await self.page.locator("#samlLoginLink").click()
    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> GradeMap:
        self.logger.info("portal.fetch.started")
        if 'infinitecampus' in self.page.url:
            from .registry import get_portal

            engine = get_portal("infinite_campus")
            return await engine(
                self.page, self.sid, self.pw, self.login_url
            ).fetch_grades()
        return {}
    # ---------------------- PARSER ------------------------------------------
