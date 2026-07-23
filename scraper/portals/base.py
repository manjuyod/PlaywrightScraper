# scraper/portals/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
import logging
from collections.abc import Mapping, MutableMapping
from typing import Any, ClassVar, Literal, cast
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup


class PortalLoggerAdapter(logging.LoggerAdapter[logging.Logger]):
    def process(  # pyright: ignore[reportImplicitOverride]
        self, msg: object, kwargs: MutableMapping[str, object]
    ) -> tuple[object, MutableMapping[str, object]]:
        merged_extra = dict(self.extra or {})
        call_extra = kwargs.get("extra")
        if isinstance(call_extra, Mapping):
            extra_fields = cast(Mapping[object, object], call_extra)
            for key, value in extra_fields.items():
                if isinstance(key, str):
                    merged_extra[key] = value
        kwargs["extra"] = merged_extra
        return msg, kwargs


class PortalEngine(ABC):
    """Interface every portal scraper must implement."""

    portal_key: ClassVar[str] = ""

    def __init__(self, page: Page, student_id: str, password: str, login_url: str, alt_portal_url: str | None = None, alt_student_id: str | None = None, alt_password: str | None = None, student_name: str | None = None, auth_images: list[str] | None = None) -> None:
        self.page: Page = page
        self.sid: str = student_id
        self.alt_sid: str | None = alt_student_id
        self.pw: str = password
        self.alt_pw: str | None = alt_password
        self.student_name: str | None = student_name
        self.auth_images: list[str] | None = auth_images
        self.login_url: str = login_url
        self.alt_portal_url: str | None = alt_portal_url
        portal_key = type(self).portal_key or type(self).__name__.lower()
        self.logger: PortalLoggerAdapter = PortalLoggerAdapter(
            logging.getLogger(f"scraper.portals.{portal_key}"),
            {"portal": portal_key},
        )
        
    @abstractmethod
    async def login(self, first_name: str | None = None) -> None: ...

    @abstractmethod
    async def fetch_grades(self) -> dict[str, Any]: ...
    
    async def get_agenda(  # pyright: ignore[reportUnusedParameter]
        self, get: Literal["upcoming", "missing"]
    ) -> dict[str, Any]: ...  # only implement if the portal has an agenda page, otherwise this will be inherited as a method that raises NotImplementedError
        
    # optional shared helpers ↓
    async def wait(self, selector: str, timeout: int = 15_000) -> None:
        await self.page.locator(selector).wait_for(state="visible", timeout=timeout)
        
    async def get_soup(self) -> BeautifulSoup:
        """Ensure the page is loaded before trying to get the soup"""
        html = await self.page.content()
        return BeautifulSoup(html, "html.parser")

    async def raise_login_error_if(self, error_condition: bool, _message: str = ""):
        """Recieves a condition on which the login has failed, raises LoginError if true"""
        if error_condition:
            raise self.LoginError("portal login rejected")

    @staticmethod
    class LoginError(Exception):
        pass

# universal flows
    async def google_login(self):
        # GOOGLE SIGN-IN
        await self.page.fill("input#identifierId", self.sid)
        await self.page.wait_for_timeout(3000)
        await self.page.get_by_text("Next").click()
        _ = await self.page.wait_for_selector('input[name="Passwd"]')
        await self.page.fill('input[name="Passwd"]', self.pw)
        await self.page.wait_for_timeout(2000)
        await self.page.get_by_role("button", name="Next").click()  # click

    async def microsoft_login(self):
        # MICROSOFT SIGN-IN
        # Fill username and password
        try:
            await self.page.fill("input#username", self.sid, timeout=1000)
            await self.page.fill("input#password", self.pw)
            # Press Enter in password field to submit the form
            await self.page.locator('.form-group input[name="password"]').press("Enter")
        except PlaywrightTimeout:
            # try with alternate tags
            await self.page.fill("input#i0116", self.sid, timeout=1000)
            await self.page.click("#idSIButton9")
            await self.page.fill("input#i0118", self.pw)
            await self.page.click("#idSIButton9")
            await self.page.wait_for_load_state()
        # did we reach a 'stay signed in' screen?
        stay_signed_in = self.page.get_by_text('Stay signed in?')
        if await stay_signed_in.count() > 0:
            await self.page.click("#idSIButton9")
        # Short pause to ensure fields are recognized
        await self.page.wait_for_timeout(1000)
