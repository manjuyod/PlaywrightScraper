from __future__ import annotations
from typing import Any, Dict, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from bs4 import BeautifulSoup

from scraper.portals.base import PortalEngine, PlaywrightTimeout
from scraper.portals import register_portal, get_portal
from .utils import *

@register_portal("google_classroom")
class GoogleClassroom(PortalEngine):
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        try: # theoretically should just use the Google sign in
            # in reality, after inserting the username the page may reroute to some internal portal
            if self.login_url != self.page.url:  # Only nav if we are not at the target page
                await self.page.goto(self.login_url, wait_until="domcontentloaded")
            try:
                await self.google_login()
                await wait_after_nav(self.page, pattern='classroom.google.com')
            except PlaywrightTimeout:
                portal = get_portal_key_from_url(self.page.url)
                if portal != 'google_classroom': # new portal reached, create a new engine and login there
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
        except Exception as e:
            print(f"{type(e)}: {e}")
            raise
        finally:
            await self.page.context.tracing.stop()

    async def get_agenda(self, get: Literal["upcoming", "missing"] = "upcoming") -> dict[str, list[tuple]]:
        agenda: dict[str, list[tuple]] = {}  # dict like {date: [(class, assignment, due_time),  ...]}
        try:
            menu_sidebar_selector = 'button[aria-label="Main Menu"]'
            await self.page.wait_for_selector(menu_sidebar_selector, timeout=10000);
            todo_tab_button = self.page.get_by_role('menuitem', name='To-do')
            # todo_tab_button = self.page.locator('a[aria-label="To-do"]')

            if not await exists(todo_tab_button):
                menu_button = self.page.locator(menu_sidebar_selector)
                assert await exists(menu_button)
                await menu_button.click() # no nav
            await todo_tab_button.click()

            upcoming_assignments_url_pattern = '**/a/not-turned-in/**'
            await wait_after_nav(self.page, pattern=upcoming_assignments_url_pattern)

            # buttons on the to-do page
            upcoming_tab_button = self.page.get_by_role('link', name='Assigned')
            missing_tab_button = self.page.get_by_role('link', name='Missing')
            assert await exists(upcoming_tab_button)
            assert await exists(missing_tab_button)
            match get:
                case "upcoming":
                    target_button = upcoming_tab_button
                    target_url = upcoming_assignments_url_pattern
                case "missing":
                    target_button = missing_tab_button
                    target_url = '**/a/missing/**'

            await target_button.click()
            await wait_after_nav(self.page, pattern=target_url, wait_after_load=2000)

            soup = await self.get_soup()

            items = soup.select('li:has(a[href*="/details"]) div[data-course-id][data-stream-item-id]')
            print(f"found {len(items)} items")

            for card in items:
                # title/course live in the first ".y9bEQb" area
                title_elem = card.select_one('.y9bEQb p')
                course_elem = card.select_one('.y9bEQb p.tWeh6') or (
                    card.select('.y9bEQb p')[1] if len(card.select('.y9bEQb p')) > 1 else None)

                # due: prefer combined, fall back to split
                due_elem = card.select_one('p.pOf0gc')
                if not due_elem:
                    due_split = card.select_one('div.nQaZq')
                    due = " ".join(p.get_text(strip=True) for p in due_split.select('p')) if due_split else None
                else:
                    due = due_elem.get_text(strip=True)

                title = title_elem.get_text(strip=True) if title_elem else None
                course = course_elem.get_text(strip=True) if course_elem else None

                due_info = reconcile_day_time(due, reference=datetime.now()) if due else (None, None)
                day: date = due_info[0] if due_info[0] else None
                due_at: time = due_info[1] if due_info[1] else None



                # print(title, course, day, due_at)
                if not title or not course or not day: continue
                due_date = day.strftime("%m/%d/%Y")
                due_time = due_at.strftime("%H:%M") if due_at else None

                if not agenda.get(due_date):
                    agenda[due_date] = [(course, title, due_time)]
                else: agenda[due_date].append((course, title, due_time))
            return agenda

            # items = soup.select('ol.e2urcc > li')
            # print(f"found {len(items)} items")
            # for li in items:
            #     title_elem = li.select_one('p.asQXV') # weird google selectors -- may be unstable
            #     course_elem = li.select_one('p.tWeh6')
            #     due_elem = li.select_one('p.pOf0gc')
            #
            #     title = normalize_whitespace(title_elem.get_text(strip=True)) if title_elem else None
            #     course = normalize_whitespace(course_elem.get_text(strip=True)) if course_elem else None
            #     due = normalize_whitespace(due_elem.get_text(strip=True)) if due_elem else None
            #
            #     due_info = reconcile_day_time(due, reference=datetime.now()) if due else (None, None)
            #
            #     day: date = due_info[0] if due_info[0] else None
            #     due_at: time = due_info[1] if due_info[1] else None
            #
            #     print(f"{day}, {due_at}: {course} - {title}")
        except Exception as e:
            import traceback
            print(e)
            traceback.print_exc()

    async def fetch_grades(self) -> Dict[str, Any]:
        try:
            table_selector = 'None'
            title_selector = 'None'
            pair_selector = 'None'
            grade_selector = 'None'
            return await grades_table_to_dict(
                self.page,
                table_selector,
                title_selector,
                grade_selector,
                pair_selector=pair_selector,
                should_truncate_before=True
            )
        except Exception as e:
            print(f"{type(e)}: {e}")
            raise
        finally:
            pass

    async def logout(self) -> None:
        await self.page.wait_for_timeout(300)
