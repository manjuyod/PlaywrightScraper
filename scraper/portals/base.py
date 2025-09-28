# scraper/portals/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List
from playwright.async_api import Page
from bs4 import BeautifulSoup
class PortalEngine(ABC):
    """Interface every portal scraper must implement."""

    def __init__(self, page: Page, student_id: str, password: str, login_url: str, alt_portal_url: str | None = None, student_name: str | None = None, auth_images: list | None = None) -> None:
        self.page, self.sid, self.pw, self.student_name, self.auth_images, self.login_url, self.alt_portal_url = page, student_id, password, student_name, auth_images, login_url, alt_portal_url
        
    @abstractmethod
    async def login(self, first_name: str | None = None) -> None: ...

    @abstractmethod
    async def fetch_grades(self) -> Dict[str, Any]: ...

    # optional shared helpers ↓
    async def _wait(self, selector: str, timeout: int = 15_000) -> None:
        await self.page.locator(selector).wait_for(state="visible", timeout=timeout)
        
    async def get_soup(self) -> BeautifulSoup:
        """Ensure the page is loaded before trying to get the soup"""
        html = await self.page.content()
        return BeautifulSoup(html, "html.parser")

    async def google_signin(self):
        # GOOGLE SIGN-IN
        await self.page.fill("input#identifierId", self.sid)
        await self.page.wait_for_timeout(3000)
        await self.page.get_by_text("Next").click()
        await self.page.wait_for_selector('input[name="Passwd"]')
        await self.page.fill('input[name="Passwd"]', self.pw)
        await self.page.wait_for_timeout(2000)
        await self.page.get_by_role("button", name="Next").click()  # click next