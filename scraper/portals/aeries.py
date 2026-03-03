from __future__ import annotations
from . import register_portal
from .base import PortalEngine
from .utils import *


@register_portal("aeries")
class Aeries(PortalEngine):
    """Portal scraper for Aeries portal.

    The class uses Playwright to automate login and extract quarter grades
    for each course. Grades are returned as a list of course/grade
    dictionaries under the ``parsed_grades`` key.
    """
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
        reraise=True,
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        """Authenticate the user on the Aeries parent portal."""
        try:
            username_selector = 'input#portalAccountUsername'
            password_selector = 'input#portalAccountPassword'
            sso_login_selector = '#LoginButton'
            await universal_login_flow(
                self.page,
                self.login_url,
                self.sid,
                self.pw,
                username_selector,
                password_selector,
                google_callback=self.google_login,
                alt_sso_callback = self.iusd_login,
                sso_login_selector=sso_login_selector
            )

            if exists(self.page.get_by_role('alert')):
                raise self.LoginError('Invalid username/password')
            await wait_after_nav(self.page, pattern='**/Dashboard**', timeout=10000)
        except Exception as e:
            print(e)
            raise
        finally:
            await self.page.context.tracing.stop()
            print("stopped tracing")

    async def iusd_login(self):
        username_selector = '#input28'
        pw_selector = '#input62'
        await universal_login_flow(
            self.page,
            self.page.url,
            self.sid,
            self.pw,
            username_selector,
            pw_selector
        )
    async def nav_to_grades(self) -> bool:
        main_grades_selector = '#NavMainGrades'
        sub_grades_selector = '#NavSubGrades'
        await self.page.click(main_grades_selector)
        sub_grades_elem = self.page.locator(sub_grades_selector)
        if await exists(sub_grades_elem):
            await self.page.click(sub_grades_selector)
            await wait_after_nav(self.page, pattern='**/Grades**', timeout=5000)
            return True
        return False

    # ---------------------- FETCH (notifications → latest per subject) -------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(PlaywrightTimeout),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        print("\nfetching grades")
        try:
            # ensure we have reached the next page
            await self.raise_login_error_if("Dashboard" not in self.page.url)
            await self.page.wait_for_timeout(3000) # wait some to allow population

            # ensure that we are on the correct dashboard, a student may have more than one
            soup = await self.get_soup()
            class_table = soup.find('div', id="divClass")
            if class_table is None:  # failed to find class table
                await self.page.click("#StudentNameDropDown")
                await self.page.click("#StudentNameDropDownMenu")
                await self.page.wait_for_load_state()
                await self.page.wait_for_timeout(3000)
            # assert class_table is not None

            grades_page_exists = await self.nav_to_grades()
            if grades_page_exists:
                table_selector = "tr[id$='ReadRow1']"
                course_selector = "td[data-tcfc='CRS.CO']"
                grade_selector = "td[data-tcfc='GRD.M2']"

                courses_dict = await grades_table_to_dict(
                    self.page,
                    table_selector,
                    course_selector,
                    grade_selector,
                    decompose_labels=True
                )
                print(f"[AERIES] parsed {len(courses_dict)}: {courses_dict}")
                return {"parsed_grades": courses_dict}
            else: # try to parse the grades from the dashboard
                print('grades tab DNE, parsing grades from dashboard')
                await self.page.reload()
                # soup = await self.get_soup()
                # print("Soup:", soup)
                courses_dict = {}
                # get class table
                if class_table is not None and len(class_table.select('div.Card')) > 0:
                    # parse the class table
                    class_cards = class_table.select('div.Card')
                    print(f"[AERIES] found {len(class_cards)}")
                    for card in class_cards:  # parse the cards
                        # course name
                        class_link = card.find("a", class_="TextHeading")
                        course_name: str = class_link.text.strip()
                        # grade
                        grade_div = card.find("div", class_="Grade")
                        grade_span = grade_div.find("span")
                        if grade_span is not None:  # as long as the grade exists
                            grade_str: str | None = grade_span.text.strip() if grade_span is not None else None
                            title = canonicalize_course_title(course_name)
                            grade = canonicalize_grade(grade_str)
                            courses_dict[title] = grade  # add to dictionary
                import pprint
                pprint.pprint(courses_dict)
                return courses_dict
        except Exception as e:
            print(e)
            raise
        finally:
            await self.page.context.tracing.stop()

    # ---------------------- LOGOUT ----------------------
    async def logout(self) -> None:
        # await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
