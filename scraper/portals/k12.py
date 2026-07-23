from __future__ import annotations
from typing import Any, Dict, Optional
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
        except Exception as e:
            self.logger.error(
                "portal.login.failed", extra={"exception_type": type(e).__name__}
            )
            raise
        finally:
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
            self.logger.error(
                "portal.fetch.failed", extra={"exception_type": type(e).__name__}
            )
        finally:
            self.logger.info(
                "portal.fetch.completed", extra={"course_count": len(parsed)}
            )
            return parsed
