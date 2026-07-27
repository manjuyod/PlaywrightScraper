from __future__ import annotations
from scraper.portals.base import (
    GradeMap,
    PortalEngine,
    PlaywrightTimeout,
    UniversalLoginConfig,
)
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

class K12(PortalEngine):
    portal_key = "k12"
    url_patterns = ("login.k12",)
    login_config = UniversalLoginConfig(
        username_selector="#okta-signin-username",
        password_selector="#okta-signin-password",
    )
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> GradeMap:
        if 'grades' not in self.page.url:
            raise self.LoginError('No grades page')
        parsed = {}
        try:
            pass
        except Exception as e:
            self.logger.error(
                "portal.fetch.failed", extra={"exception_type": type(e).__name__}
            )
        finally:
            self.logger.info(
                "portal.fetch.completed", extra={"course_count": len(parsed)}
            )
            return parsed
