from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import List, Dict, Any, Optional, Tuple

from bs4 import BeautifulSoup
from playwright.async_api import Page, Frame
from .base import PortalEngine
from . import register_portal, LoginError  # helper we'll create in __init__.py
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_if_not_exception_type


@register_portal("student_connection")
class StudentConnection(PortalEngine):
    """Portal scraper for Student Connection."""

    # ---------------------- LOGIN (home only) ----------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_not_exception_type(LoginError),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Only log in and arrive at the parent/home shell."""
        # TODO: Insert LoginError logic
        await self.page.goto(self.login_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(500)
        # TODO

    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> Dict[str, Any] | None:
        """Collect grades from the grade tab"""
        pass
    # ---------------------- LOGOUT ----------------------
    # async def logout(self) -> None:
    #     await self.page.goto(self.LOGOFF)
    #     await self.page.wait_for_timeout(500)
