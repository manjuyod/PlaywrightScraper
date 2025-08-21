# scraper/portals/blackbaud_student_bghs.py
from __future__ import annotations
import asyncio
import logging
import pathlib
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List

import re
from bs4 import BeautifulSoup  # type: ignore
from playwright.async_api import Page, Error  # type: ignore
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log
)

from .base import PortalEngine
from . import register_portal

logger = logging.getLogger("bghs_blackbaud")
logger.setLevel(logging.INFO)

DBG_DIR = pathlib.Path(__file__).resolve().parents[2] / "output" / "debug" / "blackbaud_bghs"
DBG_DIR.mkdir(parents=True, exist_ok=True)

def _ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")

async def _dump(page: Page, label: str) -> None:
    try:
        html = await page.content()
        (DBG_DIR / f"{_ts()}-{label}.html").write_text(html, encoding="utf-8")
        (DBG_DIR / f"{_ts()}-{label}.url.txt").write_text(page.url, encoding="utf-8")
        print(f"[BBG] Dumped {label} → {DBG_DIR}")
    except Exception as e:
        print(f"[BBG] Dump failed ({label}): {e}")

def _norm_grade(txt: str) -> Optional[float | str]:
    s = txt.strip()
    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", s)
    if m:  # prefer percent as float
        try:
            return float(m.group(1))
        except ValueError:
            return m.group(1)
    # fall back to letter (A, B+, etc.)
    m2 = re.search(r"\b([A-F][+-]?)\b", s, re.I)
    return m2.group(1).upper() if m2 else None

@register_portal("bghs_blackbaud")
class BlackbaudBGHS(PortalEngine):
    """Blackbaud (Bishop Gorman HS) portal scraper."""

    ENTRY = "https://signin.blackbaud.com/"
    ORG_NAME = "Bishop Gorman High School"
    PROGRESS_URL = "https://bishopgorman.myschoolapp.com/app/student#student/progress"

    # ── LOGIN ─────────────────────────────────────────────────────────────────
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=3, max=15),
        retry=retry_if_exception_type(Exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,  # <- expose inner exception instead of RetryError
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        print("[BBG] starting login()")

        # Entry page (Blackbaud SSO landing)
        await self.page.goto(self.ENTRY, wait_until="domcontentloaded", timeout=45_000)
        await self.page.wait_for_load_state("networkidle", timeout=45_000)
        await _dump(self.page, "entry")

        # Some tenants show a single “Sign in with email” then SSO choice
        # Click "Continue with SSO" if present, else proceed to email field.
        sso_btn = self.page.get_by_role("button", name=re.compile("Continue with SSO", re.I))
        if await sso_btn.count():
            await sso_btn.click(timeout=15_000)
            await _dump(self.page, "after-sso-click")

        # Email entry
        email_input = self.page.get_by_role("textbox", name=re.compile("Email", re.I))
        if not await email_input.count():
            # Some variants show a "Sign in with email" link first
            link = self.page.get_by_role("link", name=re.compile("Sign in with email", re.I))
            if await link.count():
                await link.click(timeout=15_000)
                await self.page.wait_for_timeout(600)
        await email_input.fill(self.sid, timeout=20_000)
        cont_btn = self.page.get_by_role("button", name=re.compile("Continue", re.I))
        await cont_btn.click(timeout=20_000)

        # Org selection (Bishop Gorman High School)
        await self.page.wait_for_load_state("domcontentloaded", timeout=45_000)
        await _dump(self.page, "org-prompt")
        org_btn = self.page.get_by_role("button", name=re.compile(self.ORG_NAME, re.I))
        if not await org_btn.count():
            # Sometimes renders as a link
            org_btn = self.page.get_by_role("link", name=re.compile(self.ORG_NAME, re.I))
        await org_btn.first.click(timeout=20_000)

        # Microsoft login (tenant’s AAD)
        await self.page.wait_for_url(re.compile(r"login\.microsoftonline\.com|microsoftonline\.com"), timeout=45_000)
        await _dump(self.page, "ms-login")

        # Email may prefill; if not, fill again
        try:
            ms_email = self.page.get_by_role("textbox", name=re.compile("Email|Work or school account|Sign in", re.I))
            if await ms_email.count():
                val = await ms_email.input_value()
                if not val:
                    await ms_email.fill(self.sid, timeout=20_000)
                next_btn = self.page.get_by_role("button", name=re.compile("Next", re.I))
                if await next_btn.count():
                    await next_btn.click(timeout=15_000)
        except Error:
            pass

        # Password
        await self.page.wait_for_selector('input[type="password"]', timeout=45_000)
        await self.page.fill('input[type="password"]', self.pw, timeout=20_000)
        await self.page.get_by_role("button", name=re.compile("Sign in", re.I)).click(timeout=20_000)

        # "Stay signed in?" prompt
        try:
            await self.page.wait_for_selector('input[type="submit"][value="Yes"]', timeout=10_000)
            await self.page.click('input[type="submit"][value="Yes"]', timeout=10_000)
        except Error:
            pass

        # Land on BGHS app
        await self.page.wait_for_url(re.compile(r"bishopgorman\.myschoolapp\.com"), timeout=60_000)
        await self.page.wait_for_load_state("networkidle", timeout=60_000)
        await _dump(self.page, "myschoolapp-home")

    # ── FETCH ────────────────────────────────────────────────────────────────
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=3, max=15),
        retry=retry_if_exception_type(Exception),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """Navigate to My Day → Progress, collect per-course grades via modal."""
        # Go directly to Progress (faster & consistent)
        await self.page.goto(self.PROGRESS_URL, wait_until="domcontentloaded", timeout=60_000)

        # Handle transient 502/blank with a quick re-nav
        body_txt = (await self.page.content()).lower()
        if "bad gateway" in body_txt:
            print("[BBG] Detected 502 Bad Gateway; retrying navigation once…")
            await asyncio.sleep(1.2)
            await self.page.goto(self.PROGRESS_URL, wait_until="domcontentloaded", timeout=60_000)

        # Wait for course rows to appear (buttons labeled “See grade details”)
        await self.page.wait_for_selector('button:has-text("See grade details")', timeout=60_000)
        await self.page.wait_for_load_state("networkidle", timeout=30_000)
        await _dump(self.page, "progress")

        grades: Dict[str, Any] = {}
        buttons = await self.page.locator('button:has-text("See grade details")').all()
        print(f"[BBG] Found {len(buttons)} course rows with grade details")

        # Iterate buttons by index to avoid stale handle issues after modal open/close
        for i in range(len(buttons)):
            btn = self.page.locator('button:has-text("See grade details")').nth(i)
            try:
                await btn.scroll_into_view_if_needed()
                await btn.click(timeout=20_000)
            except Error:
                # Sometimes intercepted by sticky headers; try a small scroll & retry once
                await self.page.mouse.wheel(0, 200)
                await btn.click(timeout=20_000)

            # Wait for modal content
            await self.page.wait_for_selector("div[role='dialog'], div.modal, div[aria-modal='true']", timeout=30_000)
            await self.page.wait_for_timeout(300)  # let inner content render
            await _dump(self.page, f"modal-{i+1}")

            # Parse modal: course + grade
            html = await self.page.content()
            course, grade = self._parse_modal_grade(html)
            if course and grade is not None:
                grades[course] = grade

            # Close modal (try Close button or ESC)
            closed = False
            for sel in [
                "button:has-text('Close')",
                "button[aria-label='Close']",
                "button:has-text('Done')",
            ]:
                if await self.page.locator(sel).count():
                    await self.page.locator(sel).first.click(timeout=10_000)
                    closed = True
                    break
            if not closed:
                await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(250)

        return {"parsed_grades": grades}

    # ── PARSERS ──────────────────────────────────────────────────────────────
    def _parse_modal_grade(self, html: str) -> Tuple[Optional[str], Optional[float | str]]:
        """Extract course name and grade from the opened grade-details modal."""
        soup = BeautifulSoup(html, "html.parser")
        # Course name: often appears near the modal header
        course = None
        for sel in [
            "div[role='dialog'] h2", "div.modal h2", "div[aria-modal='true'] h2",
            "div[role='dialog'] h1", "div.modal h1"
        ]:
            h = soup.select_one(sel)
            if h:
                course = h.get_text(strip=True)
                break

        # Grade: look for percentage or letter in common places
        grade = None
        # explicit “Current” / “Overall” labels often appear
        for sel in [
            "div[role='dialog']",
            "div.modal",
            "div[aria-modal='true']",
        ]:
            cont = soup.select_one(sel)
            if not cont:
                continue
            txt = " ".join(cont.get_text(" ", strip=True).split())
            g = _norm_grade(txt)
            if g is not None:
                grade = g
                break

        return course, grade
