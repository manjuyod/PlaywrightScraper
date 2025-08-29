from __future__ import annotations

import re
from datetime import datetime, timedelta  # ← added timedelta
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential, retry_if_not_exception_type, RetryError)
from .base import PortalEngine
from . import register_portal, LoginError
from playwright.async_api import Locator, Dialog, TimeoutError, Page

@register_portal("aeries_tustin")
class Aeries(PortalEngine):
    """Portal scraper for Aeries portal.

    The class uses Playwright to automate login and extract quarter grades
    for each course. Grades are returned as a list of course/grade
    dictionaries under the ``parsed_grades`` key.
    """

    LOGIN = "https://parentnet.tustin.k12.ca.us/ParentPortal/LoginParent.aspx"
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_not_exception_type(LoginError),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the user on the Aeries parent portal."""
        try:
            await self.page.context.tracing.start(screenshots=True, snapshots=True)
            await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
            # Username
            await self.page.fill("input#portalAccountUsername", self.sid)
            await self.page.wait_for_timeout(2000)
            await self.page.locator("#next").click()
            # await self.page.locator("input#portalAccountPassword").wait_for('visible')
            # Password
            await self.page.fill("input#portalAccountPassword", self.pw)
            await self.page.wait_for_timeout(2000)
            await self.page.locator("#LoginButton").click()
            #handle failed login
            errorBox: Locator = self.page.locator("#errorContainer")
            if await errorBox.is_visible():
                error_msg = await self.page.locator("#errorMessage").inner_text()
                print(f"Login Error: {error_msg}")
                raise LoginError(error_msg) 
            await self.page.wait_for_load_state('load', timeout=45000)
        finally:
            await self.page.context.tracing.stop()
            print("stopped tracing")
    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(TimeoutError),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """Stay on HOME and scrape notifications; return latest Semester Grade per subject."""
        print("\nfetching grades")
        # ensure we have reached the next page
        if "Dashboard" not in self.page.url:
            raise LoginError
        await self.page.wait_for_timeout(3000) # wait some to allow population

        soup = await self.getSoup()
        class_table = soup.find('div', id="divClass")
        
        if class_table is None:
            await self.page.click("#StudentNameDropDown")
            await self.page.click("#StudentNameDropDownMenu")
            await self.page.wait_for_load_state()
            await self.page.wait_for_timeout(3000)
            # try again with new page
            soup = await self.getSoup()
            class_table = soup.find('div', id='divClass')
                
        class_cards = class_table.select('div.Card.CardWithPeriod')
        courses_dict = {}
       
        for card in class_cards: # parse the cards
            grade_div = card.find("div", class_="Grade")
            grade_span = grade_div.find("span")
            grade_str: str | None = grade_span.text.strip() if grade_span is not None else None
            grade = float(grade_str.replace("(", "").replace(")", "").replace("%", "")) if grade_str is not None else None
            
            class_link = card.find("a", class_="TextHeading")
            class_name: str = class_link.text.strip()
            
            courses_dict[class_name.upper()] = grade
        return {"parsed_grades": courses_dict}
    
    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
