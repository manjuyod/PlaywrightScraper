
from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import ClassVar, Literal, cast

from bs4 import BeautifulSoup
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class LoginError(Exception):
    """Raised when a portal rejects a login in a recognized way."""


GradeMap = dict[str, float]


@dataclass(frozen=True)
class UniversalLoginConfig:
    username_selector: str
    password_selector: str
    sso_entry_selector: str | None = None
    microsoft_sso: bool = False
    google_sso: bool = False
    alternate_sso: bool = False
    pre_fill_wait: int = 500
    post_fill_wait: int = 1000


@dataclass(frozen=True)
class GradeTableConfig:
    table_selector: str
    title_selector: str
    grade_selector: str
    pair_selector: str | None = None
    frame_selector: str | None = None
    truncate_title_on: str | None = None
    should_truncate_before: bool = False
    decompose_labels: bool = False
    use_soup: bool = True


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


class PortalEngine:
    """Shared portal lifecycle with opt-in hooks for portal-specific behavior."""

    portal_key: ClassVar[str] = ""
    url_patterns: ClassVar[tuple[str, ...]] = ()
    login_config: ClassVar[UniversalLoginConfig | None] = None
    grade_table_config: ClassVar[GradeTableConfig | None] = None

    # Compatibility for portal code that historically raised self.LoginError.
    LoginError: ClassVar[type[LoginError]] = LoginError

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.__dict__.get("portal_key"):
            return
        from .registry import register_portal_class

        register_portal_class(cls)

    def __init__(
        self,
        page: Page,
        student_id: str,
        password: str,
        login_url: str,
        alt_portal_url: str | None = None,
        alt_student_id: str | None = None,
        alt_password: str | None = None,
        student_name: str | None = None,
        auth_images: list[str] | None = None,
    ) -> None:
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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
        reraise=True,
    )
    async def login(self, first_name: str | None = None) -> None:
        """Run a configured universal login followed by portal-specific hooks."""
        config = type(self).login_config
        if config is None:
            raise NotImplementedError(
                f"{type(self).__name__} must configure or override login()"
            )

        from .utils import universal_login_flow

        self.logger.info("portal.login.started")
        await universal_login_flow(
            self.page,
            self.login_url,
            self.sid,
            self.pw,
            config.username_selector,
            config.password_selector,
            microsoft_callback=self.microsoft_login if config.microsoft_sso else None,
            google_callback=self.google_login if config.google_sso else None,
            alt_sso_callback=(
                self.alternate_sso_login if config.alternate_sso else None
            ),
            sso_login_selector=config.sso_entry_selector,
            pre_fill_wait=config.pre_fill_wait,
            post_fill_wait=config.post_fill_wait,
        )
        await self.validate_login()
        await self.after_login(first_name)
        self.logger.info("portal.login.succeeded")

    async def validate_login(self) -> None:
        """Raise LoginError when the authenticated state is not valid."""

    async def after_login(self, first_name: str | None) -> None:
        """Perform portal-specific selection or navigation after authentication."""
        _ = first_name

    async def alternate_sso_login(self) -> None:
        raise LoginError("portal login rejected")

    async def fetch_grades(self) -> GradeMap:
        """Parse a declaratively configured grade table."""
        config = type(self).grade_table_config
        if config is None:
            raise NotImplementedError(
                f"{type(self).__name__} must configure or override fetch_grades()"
            )

        from .utils import grades_table_to_dict

        return await grades_table_to_dict(
            self.page,
            config.table_selector,
            config.title_selector,
            config.grade_selector,
            pair_selector=config.pair_selector,
            frame_selector=config.frame_selector,
            truncate_title_on=config.truncate_title_on,
            should_truncate_before=config.should_truncate_before,
            decompose_labels=config.decompose_labels,
            use_soup=config.use_soup,
        )

    async def get_agenda(
        self, get: Literal["upcoming", "missing"]
    ) -> dict[str, object]:
        _ = get
        raise NotImplementedError

    async def wait(self, selector: str, timeout: int = 15_000) -> None:
        await self.page.locator(selector).wait_for(state="visible", timeout=timeout)

    async def get_soup(self) -> BeautifulSoup:
        html = await self.page.content()
        return BeautifulSoup(html, "html.parser")

    async def raise_login_error_if(
        self, error_condition: bool, _message: str = ""
    ) -> None:
        if error_condition:
            raise LoginError("portal login rejected")

    async def google_login(self) -> None:
        await self.page.fill("input#identifierId", self.sid)
        await self.page.wait_for_timeout(3000)
        await self.page.get_by_text("Next").click()
        _ = await self.page.wait_for_selector('input[name="Passwd"]')
        await self.page.fill('input[name="Passwd"]', self.pw)
        await self.page.wait_for_timeout(2000)
        await self.page.get_by_role("button", name="Next").click()

    async def microsoft_login(self) -> None:
        try:
            await self.page.fill("input#username", self.sid, timeout=1000)
            await self.page.fill("input#password", self.pw)
            await self.page.locator('.form-group input[name="password"]').press("Enter")
        except PlaywrightTimeout:
            await self.page.fill("input#i0116", self.sid, timeout=1000)
            await self.page.click("#idSIButton9")
            await self.page.fill("input#i0118", self.pw)
            await self.page.click("#idSIButton9")
            await self.page.wait_for_load_state()

        stay_signed_in = self.page.get_by_text("Stay signed in?")
        if await stay_signed_in.count() > 0:
            await self.page.click("#idSIButton9")
        await self.page.wait_for_timeout(1000)
