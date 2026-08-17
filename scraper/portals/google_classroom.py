from __future__ import annotations
from typing import Optional
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from datetime import datetime
from playwright.async_api import Page

from scraper.agenda_contract import AgendaRecord, AgendaStatus
from scraper.portals.base import GradeMap, LoginError, PortalEngine, PlaywrightTimeout
from scraper.portals import get_portal
from .utils import exists, wait_after_nav, reconcile_day_time, get_portal_key_from_url


_CLASSROOM_ORIGIN = "https://classroom.google.com"
_GOOGLE_CREDENTIAL_ORIGIN = "https://accounts.google.com"
_GOOGLE_CREDENTIAL_SUBMITTED = "_google_classroom_credentials_submitted"


class GoogleClassroomAgendaError(Exception):
    def __init__(self) -> None:
        super().__init__("google_classroom_agenda_failed")


def _normalized_https_origin(url: str | None) -> str | None:
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return None
    return f"https://{parsed.hostname.casefold()}"


class _GoogleCredentialSubmitter:
    def __init__(self, page: Page, student_id: str, password: str) -> None:
        self.page = page
        self.student_id = student_id
        self.password = password

    def _require_credential_origin(self) -> None:
        if _normalized_https_origin(self.page.url) != _GOOGLE_CREDENTIAL_ORIGIN:
            raise LoginError("portal login rejected")

    async def submit_once(self) -> None:
        self._require_credential_origin()
        if getattr(self.page, _GOOGLE_CREDENTIAL_SUBMITTED, False):
            raise LoginError("portal login rejected")
        setattr(self.page, _GOOGLE_CREDENTIAL_SUBMITTED, True)

        await self.page.fill("input#identifierId", self.student_id)
        await self.page.wait_for_timeout(3000)
        self._require_credential_origin()
        await self.page.get_by_text("Next").click()
        self._require_credential_origin()
        _ = await self.page.wait_for_selector('input[name="Passwd"]')
        self._require_credential_origin()
        await self.page.fill('input[name="Passwd"]', self.password)
        await self.page.wait_for_timeout(2000)
        self._require_credential_origin()
        await self.page.get_by_role("button", name="Next").click()


async def _has_classroom_main_menu(page: object) -> bool:
    return _normalized_https_origin(
        getattr(page, "url", None)
    ) == _CLASSROOM_ORIGIN and await exists(
        page.locator('button[aria-label="Main Menu"]'), timeout=10_000
    )


async def _require_agenda_control(control: object) -> None:
    if not await exists(control, timeout=10_000):
        raise GoogleClassroomAgendaError()


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

        try:
            due_date, due_time = reconcile_day_time(due, reference=reference)
        except ValueError:
            continue
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
        _ = first_name
        try:
            configured_origin = _normalized_https_origin(self.login_url)
            if configured_origin not in (
                _CLASSROOM_ORIGIN,
                _GOOGLE_CREDENTIAL_ORIGIN,
            ):
                raise LoginError("portal login rejected")
            if await _has_classroom_main_menu(self.page):
                return

            if await self._delegate_current_portal():
                return

            current_origin = _normalized_https_origin(self.page.url)
            if current_origin not in (
                None,
                _CLASSROOM_ORIGIN,
                _GOOGLE_CREDENTIAL_ORIGIN,
            ):
                raise LoginError("portal login rejected")
            if current_origin is None and self.page.url != "about:blank":
                raise LoginError("portal login rejected")
            if current_origin != _GOOGLE_CREDENTIAL_ORIGIN:
                await self.page.goto(self.login_url, wait_until="domcontentloaded")

            if await _has_classroom_main_menu(self.page):
                return
            if await self._delegate_current_portal():
                return

            try:
                await self.google_login()
                await wait_after_nav(self.page, pattern='classroom.google.com')
            except PlaywrightTimeout:
                pass

            if await _has_classroom_main_menu(self.page):
                return
            if not await self._delegate_current_portal():
                raise LoginError("portal login rejected")
        except LoginError:
            raise LoginError("portal login rejected") from None
        except Exception:
            raise LoginError("portal login rejected") from None

    async def google_login(self) -> None:
        await _GoogleCredentialSubmitter(self.page, self.sid, self.pw).submit_once()

    async def _delegate_current_portal(self) -> bool:
        redirect_origin = _normalized_https_origin(self.page.url)
        configured_origin = _normalized_https_origin(self.alt_portal_url)
        redirect_portal = get_portal_key_from_url(self.page.url)
        configured_portal = get_portal_key_from_url(self.alt_portal_url or "")
        approved_delegation = (
            redirect_origin is not None
            and redirect_origin == configured_origin
            and redirect_portal is not None
            and redirect_portal == configured_portal
            and redirect_portal != self.portal_key
            and self.alt_sid is not None
            and self.alt_pw is not None
        )
        if not approved_delegation:
            return False
        if getattr(self.page, "_google_classroom_delegated", False):
            raise LoginError("portal login rejected")
        setattr(self.page, "_google_classroom_delegated", True)

        Engine = get_portal(redirect_portal)
        scraper = Engine(
            self.page,
            self.alt_sid,
            self.alt_pw,
            login_url=self.page.url,
            student_name=self.student_name,
            auth_images=list(self.auth_images)
            if self.auth_images is not None
            else None,
        )
        await scraper.login()
        if not await _has_classroom_main_menu(self.page):
            raise LoginError("portal login rejected")
        return True

    async def get_agenda(self) -> list[AgendaRecord]:
        try:
            menu_sidebar_selector = 'button[aria-label="Main Menu"]'
            await self.page.wait_for_selector(menu_sidebar_selector, timeout=10000)
            todo_tab_button = self.page.get_by_role('menuitem', name='To-do')

            if not await exists(todo_tab_button, timeout=10_000):
                menu_button = self.page.locator(menu_sidebar_selector)
                await _require_agenda_control(menu_button)
                await menu_button.click()
                await _require_agenda_control(todo_tab_button)
            await todo_tab_button.click()

            upcoming_assignments_url_pattern = '**/a/not-turned-in/**'
            await wait_after_nav(self.page, pattern=upcoming_assignments_url_pattern)

            upcoming_tab_button = self.page.get_by_role('link', name='Assigned')
            missing_tab_button = self.page.get_by_role('link', name='Missing')
            await _require_agenda_control(upcoming_tab_button)
            await _require_agenda_control(missing_tab_button)
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
