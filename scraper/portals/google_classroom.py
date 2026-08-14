from __future__ import annotations
from typing import Optional
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from datetime import datetime

from scraper.agenda_contract import AgendaRecord, AgendaStatus
from scraper.portals.base import GradeMap, PortalEngine, PlaywrightTimeout
from scraper.portals import get_portal
from .utils import exists, wait_after_nav, reconcile_day_time, get_portal_key_from_url


class GoogleClassroomAgendaError(Exception):
    def __init__(self) -> None:
        super().__init__("google_classroom_agenda_failed")


def _parse_classroom_agenda(
    html: str, status: AgendaStatus, *, reference: datetime
) -> list[AgendaRecord]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[AgendaRecord] = []
    items = soup.select('li:has(a[href*="/details"]) div[data-course-id][data-stream-item-id]')
    for card in items:
        title_elem = card.select_one(".y9bEQb p")
        course_elem = card.select_one(".y9bEQb p.tWeh6") or (
            card.select(".y9bEQb p")[1]
            if len(card.select(".y9bEQb p")) > 1
            else None
        )
        due_elem = card.select_one("p.pOf0gc")
        if due_elem is None:
            due_split = card.select_one("div.nQaZq")
            due = (
                " ".join(part.get_text(strip=True) for part in due_split.select("p"))
                if due_split is not None
                else None
            )
        else:
            due = due_elem.get_text(strip=True)

        title = title_elem.get_text(strip=True) if title_elem is not None else None
        course = course_elem.get_text(strip=True) if course_elem is not None else None
        source_id = card.get("data-stream-item-id")
        if not title or not course or not due or not source_id:
            continue

        due_date, due_time = reconcile_day_time(due, reference=reference)
        records.append(
            {
                "sourceId": f"google_classroom:{source_id}",
                "course": course,
                "title": title,
                "dueDate": due_date.isoformat(),
                "dueTime": due_time.strftime("%H:%M") if due_time else None,
                "status": status,
            }
        )
    return records


class GoogleClassroom(PortalEngine):
    portal_key = "google_classroom"
    url_patterns = ("classroom.google", "accounts.google")
    agenda_capable = True

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try: # in theory, we should just use the Google sign in
            # in reality, after inserting the username, the page may reroute to some internal portal
            if self.login_url != self.page.url:  # Only nav if we are not at the target page
                await self.page.goto(self.login_url, wait_until="domcontentloaded")
            try:
                await self.google_login()
                await wait_after_nav(self.page, pattern='classroom.google.com')
            except PlaywrightTimeout:
                portal = get_portal_key_from_url(self.page.url)
                if portal and portal != 'google_classroom': # new portal reached, create a new engine and login there
                    Engine = get_portal(portal)
                    scraper = Engine(
                        self.page,
                        self.sid,
                        self.pw,
                        login_url=self.page.url,
                        student_name=self.student_name,
                        auth_images=self.auth_images
                    )
                    await scraper.login()
        except Exception:
            raise

    async def get_agenda(self) -> list[AgendaRecord]:
        try:
            menu_sidebar_selector = 'button[aria-label="Main Menu"]'
            await self.page.wait_for_selector(menu_sidebar_selector, timeout=10000)
            todo_tab_button = self.page.get_by_role('menuitem', name='To-do')

            if not await exists(todo_tab_button):
                menu_button = self.page.locator(menu_sidebar_selector)
                assert await exists(menu_button)
                await menu_button.click()
            await todo_tab_button.click()

            upcoming_assignments_url_pattern = '**/a/not-turned-in/**'
            await wait_after_nav(self.page, pattern=upcoming_assignments_url_pattern)

            upcoming_tab_button = self.page.get_by_role('link', name='Assigned')
            missing_tab_button = self.page.get_by_role('link', name='Missing')
            assert await exists(upcoming_tab_button)
            assert await exists(missing_tab_button)
            await upcoming_tab_button.click()
            await wait_after_nav(
                self.page, pattern=upcoming_assignments_url_pattern, wait_after_load=2000
            )
            due_records = _parse_classroom_agenda(
                await self.page.content(), "due", reference=datetime.now()
            )

            await missing_tab_button.click()
            await wait_after_nav(
                self.page, pattern='**/a/missing/**', wait_after_load=2000
            )
            missing_records = _parse_classroom_agenda(
                await self.page.content(), "missing", reference=datetime.now()
            )
            return due_records + missing_records
        except GoogleClassroomAgendaError:
            raise
        except Exception:
            raise GoogleClassroomAgendaError() from None

    async def fetch_grades(self) -> GradeMap:
        return {}

    async def logout(self) -> None:
        await self.page.wait_for_timeout(300)
