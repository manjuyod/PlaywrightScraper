from __future__ import annotations
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional

from scraper.portals.base import PortalEngine
from scraper.portals import register_portal
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from playwright.async_api import TimeoutError as PlaywrightTimeout

@register_portal("parentvue_husd")
class ParentVUE_HUSD(PortalEngine):
    LOGIN = "https://parentvue.husd.org/PXP2_Login_Parent.aspx"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        # 1) Load login page
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(400)

        # 2) Fill & submit
        await self.page.fill("#ctl00_MainContent_username", self.sid)
        await self.page.fill("#ctl00_MainContent_password", self.pw)
        await self.page.click("#ctl00_MainContent_Submit1")

        # 3) Post-login settle without relying on networkidle
        # 15 second pause more than suffices
        await self.page.wait_for_timeout(8000)
        await self.select_student(first_name)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def select_student(self, first_name: Optional[str] = None) -> str:
        """
        Open the student dropdown, click the matching student,
        grab its data-agu, then fetch and return the Gradebook HTML.
        """
        target = (first_name or getattr(self, "student_name", "") or "").strip()
        if not target:
            raise RuntimeError("No first_name provided to select_student()")

        target_lc = target.lower()

        # base selectors
        header_sel = "#ctl00_ctl00_MainContent_PXPHeader"
        selector_root = f"{header_sel} #ctl00_ctl00_MainContent_StudentSelector"
        current_button = f"{selector_root} .current"
        menu_ul = f"{selector_root} ul.dropdown-menu"
        item_info = f"{menu_ul} .student-info"
        current_name_sel = f"{selector_root} .current .student-name"

        # 1) If we're already on the right student, bail and grab its AGU
        try:
            name = (await self.page.locator(current_name_sel).inner_text()).strip()
            if target_lc in name.lower():
                # find its data-agu in the .current container
                agu = await self.page.locator(f"{selector_root} .current .student-info").get_attribute("data-agu")
                if not agu:
                    raise RuntimeError("Could not read data-agu from current student")
                grade_url = f"https://parentvue.husd.org/PXP2_Gradebook.aspx?AGU={agu}"
                print(f"[PARENTVUE] Already on {name}; AGU={agu}; navigating to grades")
                await self.page.goto(grade_url, wait_until="domcontentloaded")
                return await self.page.content()
        except Exception:
            # continue to the dropdown approach
            pass

        # 2) Open the dropdown
        await self.page.click(current_button)
        await self.page.wait_for_selector(menu_ul, timeout=5000)

        # 3) Find and click the matching student-info
        items = self.page.locator(item_info)
        n = await items.count()
        if n == 0:
            raise RuntimeError("No student items in dropdown")

        agu = None
        for i in range(n):
            info = items.nth(i)
            name = (await info.locator(".student-name").inner_text()).strip()
            if target_lc in name.lower():
                agu = await info.get_attribute("data-agu")
                await info.click()
                print(f"[PARENTVUE] Clicked student '{name}' (AGU={agu})")
                break

        if not agu:
            raise RuntimeError(f"No dropdown student matched '{target}'")

        # 4) Navigate to gradebook using that AGU
        grade_url = f"https://parentvue.husd.org/PXP2_Gradebook.aspx?AGU={agu}"
        print(f"[PARENTVUE] Navigating to Gradebook → {grade_url}")
        await self.page.goto(grade_url, wait_until="domcontentloaded")
        # give it a moment to render
        await self.page.wait_for_timeout(800)
        return await self.page.content()
        
    async def fetch_grades(self) -> Dict[str, Any]:
        await self.page.pause()
        return {"parsed_grades": {}}

    async def logout(self) -> None:
        await self.page.pause()
