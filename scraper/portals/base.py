# scraper/portals/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout
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
    async def wait(self, selector: str, timeout: int = 15_000) -> None:
        await self.page.locator(selector).wait_for(state="visible", timeout=timeout)
        
    async def get_soup(self) -> BeautifulSoup:
        """Ensure the page is loaded before trying to get the soup"""
        html = await self.page.content()
        return BeautifulSoup(html, "html.parser")

    async def raise_if_login_error(self, error_condition: bool):
        """Recieves a condition on which the login has failed, raises LoginError if true"""
        if error_condition:
            raise self.LoginError(f'@{self.login_url}\nFailed to login {self.sid}')

    @staticmethod
    def percent_from_letter_grade(letter_grade: str):
        minus = letter_grade.endswith("-")
        plus = letter_grade.endswith("+")
        modifier = -5 if minus else 5 if plus else 0
        grade = 95
        if modifier != 0:
            letter_grade = letter_grade.replace("-", "")
            letter_grade = letter_grade.replace("+", "")
        match letter_grade:
            case 'A':
                pass
            case 'B':
                grade -= 11  # 89
            case 'C':
                grade -= 21  # 79
            case 'D':
                grade -= 31  # 69
            case 'F':
                grade -= 40  # 60
            case _:
                grade = -1
        return grade + modifier if grade > 0 else grade
    class LoginError(Exception):
        pass

# universal flows
    async def google_signin(self):
        # GOOGLE SIGN-IN
        await self.page.fill("input#identifierId", self.sid)
        await self.page.wait_for_timeout(3000)
        await self.page.get_by_text("Next").click()
        await self.page.wait_for_selector('input[name="Passwd"]')
        await self.page.fill('input[name="Passwd"]', self.pw)
        await self.page.wait_for_timeout(2000)
        await self.page.get_by_role("button", name="Next").click()  # click

    async def microsoft_login(self):
        # MICROSOFT SIGN-IN
        # Fill username and password
        await self.page.fill("input#username", self.sid)
        await self.page.fill("input#password", self.pw)
        # Short pause to ensure fields are recognized
        await self.page.wait_for_timeout(200)
        # Press Enter in password field to submit the form
        await self.page.locator('.form-group input[name="password"]').press("Enter")