# scraper/portals/infinite_campus.py
from bs4 import BeautifulSoup
from typing import List, Dict, Any
from playwright.async_api import Page
from .base import PortalEngine
from . import register_portal  # helper we'll create in __init__.py
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from utils.ratelimiter import global_limiter
import random

@register_portal("infinite_campus_student_ccsd")
class InfiniteCampus(PortalEngine):
    LOGIN = "https://campus.ccsd.net/campus/portal/students/clark.jsp"
    GRADEBOOK  = "https://campus.ccsd.net/campus/nav-wrapper/student/portal/student/grades?appName=clark"      # substring check works fine
    LOGOFF = "https://campus.ccsd.net/campus/portal/students/clark.jsp?status=logoff"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception)
    )
    async def login(self) -> None:
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
        await self.page.fill("input#username", self.sid)
        await self.page.fill("input#password", self.pw)

        #  tiny debounce avoids "click happened before element is enabled".
        await self.page.wait_for_timeout(200)

        # After filling both fields …
        await self.page.locator('.form-group input[name="password"]').press("Enter")

        # …and still wait for the redirect so Playwright knows it succeeded
        await self.page.wait_for_url(lambda url: "home" in url, timeout=15_000)

        #await self.page.pause()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception)
    )
    async def fetch_grades(self) -> dict:
        await self.page.goto(self.GRADEBOOK, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(3000)  # Wait for 3 secondss
        await self.page.wait_for_selector("iframe#main-workspace", timeout=15_000)
        await self.page.wait_for_function(
            """() => {
                const f = document.querySelector('iframe#main-workspace');
                return f && f.src && f.src.includes('/apps/portal/student/grades');
            }""",
            timeout=15_000,
        )
        frame = self.page.frame(
            url=lambda u: "/apps/portal/student/grades" in u
        )
        if not frame:
            raise RuntimeError("Grade iframe never loaded")
        await frame.wait_for_load_state("networkidle")
        html_dump = await frame.content()                      # raw iframe HTML
        parsed = self._parse_quarter_grades(html_dump)
        return {
            #"raw_html": html_dump,
            "parsed_grades": parsed
            }          # <<–– wrap in dict

    # Grade Parser Function
    def _parse_quarter_grades(self, html: str) -> List[Dict[str, Any]]:
        """Extract quarter grades (letter + percentage) from grade-page HTML."""
        soup     = BeautifulSoup(html, "html.parser")
        courses  = []

        # course cards
        for card in soup.select("div.collapsible-card.grades__card"):
            header = card.find("tl-grading-section-header")
            if not header:
                continue

            # course name (link or h4 fallback)
            name_tag = header.find("a") or header.find("h4")
            if not name_tag:
                continue
            course_name = name_tag.get_text(strip=True)

            task_list = card.find("tl-grading-task-list")
            if not task_list:
                continue

            quarter_grade = None
            for li in task_list.find_all("li"):
                grade_type = li.find("span", class_="ng-star-inserted")
                if not grade_type or "Quarter Grade" not in grade_type.text:
                    continue

                score_span = li.find("tl-grading-score")
                if not score_span:
                    continue

                grade_data = {"type": grade_type.text.strip()}

                # first <b> → letter grade
                letter_b = score_span.find("b")
                if letter_b:
                    grade_data["letter_grade"] = letter_b.text.strip()

                # any <b>(xx.x%) → percentage
                for b in score_span.find_all("b"):
                    txt = b.text.strip()
                    if txt.startswith("(") and "%" in txt:
                        try:
                            grade_data["percentage"] = float(txt.strip("()%"))
                        except ValueError:
                            grade_data["percentage_raw"] = txt
                        break

                quarter_grade = grade_data
                break  # only one per course

            if quarter_grade:
                courses.append(
                    {"course_name": course_name, "quarter_grade": quarter_grade}
                )

        return courses