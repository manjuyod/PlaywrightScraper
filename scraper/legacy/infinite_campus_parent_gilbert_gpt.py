"""
Infinite Campus scraper for Gilbert Public Schools (parent portal).

This module contains an updated version of the existing scraper found in
``scraper/portals/infinite_campus_parent_gilbert.py`` from the
``PlaywrightScraper`` repository.  The primary changes address
issues observed when running against the current (as of August 2025)
Gilbert Public Schools parent portal:

* The student‑selection routine now includes a generic fallback.  If
  the modern ``app-student-summary-button`` component or an anchor
  containing ``personID=`` is not found, the scraper will search for
  *any* element containing the target first name and click it.  This
  helps when the Infinite Campus UI changes class names or markup
  structure.

* Grade extraction has been made more robust.  The previous
  implementation assumed that a grades page would load inside an
  ``iframe#main-workspace``.  In the current portal the grade cards
  render directly in the main document without an iframe.  The
  ``fetch_grades`` method now attempts to locate the iframe and, if
  absent, falls back to parsing the top‑level page.  It also waits
  explicitly for grade cards to load before collecting the HTML.

This file can be dropped in place of the existing engine or used as
a reference for updating the original code.  No external behaviour
has changed beyond the bug fixes described above.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
from bs4 import BeautifulSoup  # type: ignore
from urllib.parse import urljoin
from playwright.async_api import Page  # type: ignore

# Import the base PortalEngine and register decorator from the original
# scraper package.  When integrating this file back into the
# PlaywrightScraper repository you can remove the relative import
# prefixes.
from scraper.portals.base import PortalEngine  # type: ignore
from scraper.portals import register_portal  # type: ignore

# Import the GPT-driven helper from utils.  This helper uses OpenAI
# to identify and click the appropriate student card.  When the
# environment variable OPENAI_API_KEY is not set, the helper returns
# False and selection falls back to heuristic logic.
from utils.ccsd_parent_gpt import click_student_card

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


@register_portal("infinite_campus_parent_gilbert_gpt")
class InfiniteCampus(PortalEngine):
    """Scraper implementation for the Gilbert, AZ Infinite Campus parent portal.

    The updated implementation fixes student selection and grade parsing
    for the modern portal layout.
    """

    LOGIN: str = "https://gilbertaz.infinitecampus.org/campus/gilbert.jsp"
    GRADEBOOK: str = (
        "https://gilbertaz.infinitecampus.org/campus/nav-wrapper/parent/portal/parent/grades?appName=gilbert"
    )
    LOGOFF: str = (
        "https://gilbertaz.infinitecampus.org/campus/portal/parents/gilbert.jsp?status=logoff"
    )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def login(self, first_name: Optional[str] = None) -> None:
        await self.page.context.tracing.start(screenshots=True, snapshots=True)
        await self.page.goto(self.LOGIN, wait_until="domcontentloaded")
        await self.page.fill("input#username", self.sid)
        await self.page.fill("input#password", self.pw)
        await self.page.wait_for_timeout(200)
        await self.page.click("button:has-text('Log In')")
        await self.page.wait_for_url(lambda url: "home" in url or "personID=" in url,
                                     timeout=15_000)
        await self.page.wait_for_load_state("networkidle")

        target_name: Optional[str] = first_name or getattr(self, "student_name", None)
        if target_name:
            print(f"[GILBERT]  Trying GPT navigation for {target_name!r}")
            ok = await click_student_card(self.page, target_name)
            print(f"[GILBERT]  GPT navigation success? {ok}")
            if not ok:
                print("[GILBERT]  Falling back to deterministic selector.")
                await self._select_student_by_first_name(target_name)
        # Save the page HTML for debugging (optional)
        try:
            with open("gilbert_parent_portal.html", "w", encoding="utf-8") as f:
                f.write(await self.page.content())
        except Exception:
            # Ignore errors saving the debug file
            pass

    async def _select_student_by_first_name(self, first_name: str) -> None:
        """Select a student on the home page by clicking the appropriate card.

        Supports both the modern Angular component layout and older
        anchor‑based layouts.  Falls back to clicking any element
        containing the student's first name if no structured controls
        are found.

        Args:
            first_name: The first name to search for within the student’s
                displayed name.

        Raises:
            RuntimeError: If no matching student control is found.
        """
        # Wait for either summary buttons or anchor links to appear
        # Wait for either summary buttons or anchor links to appear.  We do not
        # wait on a ``text=<name>`` selector here because Playwright treats
        # ``text=...`` selectors differently than CSS selectors and they
        # cannot be combined with a comma‑separated selector list.  The
        # fallback click later will handle the generic text search.
        await self.page.wait_for_selector(
            "app-student-summary-button, a[href*='personID=']",
            timeout=15_000,
        )

        # --- Modern layout: app-student-summary-button ---
        summary_buttons = self.page.locator("app-student-summary-button")
        try:
            summary_count = await summary_buttons.count()
        except Exception:
            summary_count = 0
        for idx in range(summary_count):
            btn = summary_buttons.nth(idx)
            # Each summary button contains an element with class "studentSummary__student-name"
            name_locator = btn.locator(".studentSummary__student-name")
            if await name_locator.count() > 0:
                try:
                    text = (await name_locator.inner_text()).strip()
                except Exception:
                    continue
                # Do a substring match rather than prefix match
                if first_name.lower() in text.lower():
                    # Click the button's inner clickable element if it exists; otherwise click the component itself
                    click_target = btn.locator(".studentSummary__button")
                    if await click_target.count() > 0:
                        await click_target.click()
                    else:
                        await btn.click()
                    # Wait until the URL updates to include personID (indicates student is selected)
                    await self.page.wait_for_url(
                        lambda url: "personID=" in url, timeout=10_000
                    )
                    return

        # --- Older layout: anchor tags with personID query ---
        student_links = self.page.locator("a[href*='personID=']")
        try:
            link_count = await student_links.count()
        except Exception:
            link_count = 0
        for idx in range(link_count):
            link = student_links.nth(idx)
            try:
                text = (await link.inner_text()).strip()
            except Exception:
                continue
            # Substring match on the anchor's text
            if first_name.lower() in text.lower():
                href = await link.get_attribute("href")
                if href:
                    full_url = urljoin("https://gilbertaz.infinitecampus.org", href)
                    await self.page.goto(full_url, wait_until="domcontentloaded")
                    await self.page.wait_for_load_state("networkidle")
                    return
                else:
                    await link.click()
                    await self.page.wait_for_url(
                        lambda url: "personID=" in url, timeout=10_000
                    )
                    return

        # --- Final fallback: click any element containing the first name ---
        # Some Infinite Campus layouts do not use explicit anchor tags or
        # component selectors for the student cards.  Instead of giving
        # up, attempt to click the first element that includes the
        # desired first name.  This is a broad match but is scoped to
        # just the first result so as not to interact with unrelated
        # elements (e.g., inbox messages or announcements).
        candidate = self.page.locator(f"text={first_name}").first
        try:
            # Ensure the candidate is visible on screen before clicking
            await candidate.scroll_into_view_if_needed()
            await candidate.click()
            # Wait until the personID appears in the URL or the network
            # goes idle, indicating the profile page has loaded.
            await self.page.wait_for_url(
                lambda url: "personID=" in url, timeout=10_000
            )
            await self.page.wait_for_load_state("networkidle")
            return
        except Exception:
            # Fall through to raising an error
            pass

        # If all methods fail, raise an error
        raise RuntimeError(
            f"Student with first name '{first_name}' not found on home page"
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type(Exception),
    )
    async def fetch_grades(self) -> Dict[str, Any]:
        """Fetch semester grades for the currently selected student.

        The gradebook may load inside an iframe or directly in the main
        document depending on the Infinite Campus version.  This method
        attempts to locate the iframe first and falls back to parsing
        the top‑level page when no iframe is present.  It waits for
        grade cards to appear before collecting the HTML for parsing.
        """
        await self.page.goto(self.GRADEBOOK, wait_until="domcontentloaded")
        # Allow some time for dynamic content to load
        await self.page.wait_for_timeout(3_000)
        html_dump: Optional[str] = None
        frame = None
        # Try to locate the expected iframe.  A short timeout is used so
        # that we quickly fall back to the top‑level page when no iframe
        # exists.  Playwright will raise on timeout, which we catch.
        try:
            # Wait up to 10 seconds for the grade iframe to appear.  A longer
            # timeout helps on slower network connections.
            await self.page.wait_for_selector("iframe#main-workspace", timeout=10_000)
            frame = self.page.frame(url=lambda u: "/apps/portal/parent/grades" in u if u else False)
        except Exception:
            frame = None
        if frame:
            # Wait for the iframe to finish network activity before
            # extracting its HTML.
            await frame.wait_for_load_state("networkidle")
            html_dump = await frame.content()
        else:
            # No iframe; wait for grade cards to be rendered in the main page.
            # The classes used here (``collapsible-card`` and ``grades__card``)
            # correspond to the modern Infinite Campus UI.  If the page
            # changes, this selector may need to be updated.
            await self.page.wait_for_selector(
                "div.collapsible-card.grades__card, div.collapsible-card, div.card",
                timeout=30_000,
            )
            # Additional wait for network idle to ensure grades are fully loaded
            await self.page.wait_for_load_state("networkidle")
            html_dump = await self.page.content()
        # Parse the HTML using our helper
        parsed = self._parse_semester_grades(html_dump or "")
        return {
            "parsed_grades": parsed,
        }

    # ---------------------- PARSER ----------------------
    def _parse_semester_grades(self, html: str) -> List[Dict[str, Any]]:
        """
        Return: [{'Course Name': 93.4}, {'Chemistry': 'A'}, ...]
        - Prefers percentage (as float) over letter.
        - Handles multiple Infinite Campus layouts.
        - Emits verbose debug prints and dumps the source HTML.
        """
        # --- 0) Dump HTML for one-run debugging (safe to keep; small files) ---
        try:
            Path("debug_gradebook.html").write_text(html, encoding="utf-8")
            print("[GRADES] Wrote debug_gradebook.html")
        except Exception as e:
            print("[GRADES] Could not write debug file:", e)

        soup = BeautifulSoup(html or "", "html.parser")
        results: List[Dict[str, Any]] = []

        def extract_pct_and_letter(container) -> Tuple[Optional[float], Optional[str]]:
            """Try to pull percentage and letter from a container fragment."""
            pct: Optional[float] = None
            letter: Optional[str] = None

            # Percentage: any text node with a %
            pct_node = container.find(string=lambda t: isinstance(t, str) and "%" in t)
            if pct_node:
                raw = pct_node.strip()
                try:
                    # Strip parentheses and percent sign e.g. "(92.3%)" → 92.3
                    num = raw.strip("()%").replace(",", "")
                    pct = float(num)
                except ValueError:
                    pass

            # Letter: bold/strong or data-test holder (fallback)
            letter_node = (
                container.find(["b", "strong"])
                or container.select_one("[data-test='finalGrade']")
            )
            if letter_node and hasattr(letter_node, "get_text"):
                txt = letter_node.get_text(strip=True)
                # Ignore if the "letter" is actually a percent text
                if not any(ch.isdigit() for ch in txt):
                    letter = txt

            return pct, letter

        # --- 1) Modern "card" layout (various skins) ---------------------------
        card_sel = "div.collapsible-card.grades__card, section.grades__course-card"
        cards = soup.select(card_sel)
        print(f"[GRADES] Card containers found: {len(cards)}")

        for card in cards:
            # Course name candidates
            name_tag = card.select_one("h4 a, h4, h3.courseName, .student-course-name, .courseName")
            if not name_tag:
                # Some skins put the name in a header component
                name_tag = card.find(["h3", "h4"])

            if not name_tag:
                continue

            course = name_tag.get_text(strip=True)
            row = None

            # Look for a row that mentions "Semester Grade"
            for li in card.select("li"):
                if "Semester Grade" in li.get_text():
                    row = li
                    break

            # If not present, try data-test holder
            if row is None:
                row = card.select_one("[data-test='finalGrade']")

            if row is None:
                continue

            pct, letter = extract_pct_and_letter(row)
            if pct is not None:
                results.append({course: pct})
            elif letter is not None:
                results.append({course: letter})

        # --- 2) Table layout (common in some portals/semesters) ----------------
        # Rows often have an explicit final-grade cell.
        if not results:
            rows = soup.select("tr.tl-table__row, tr.ic-IndexRow, table tr")
            print(f"[GRADES] Table rows scanned: {len(rows)}")

            for row in rows:
                # Try to identify the course cell
                course_cell = (
                    row.select_one(".tl-table__cell--courseName, .courseName")
                    or (row.find("td") if row.find("td") else None)
                )
                grade_cell = row.select_one("[data-test='finalGrade'], .final-grade, td.finalGrade")

                if not course_cell or not grade_cell:
                    continue

                course = course_cell.get_text(strip=True)
                pct, letter = extract_pct_and_letter(grade_cell)
                if pct is not None:
                    results.append({course: pct})
                elif letter:
                    results.append({course: letter})

        # --- 3) Fallback: any finalGrade block paired with nearest course name -
        if not results:
            finals = soup.select("[data-test='finalGrade']")
            print(f"[GRADES] Fallback pass: finalGrade blocks={len(finals)}")
            for fg in finals:
                # Walk up to find a plausible container with a course title
                container = fg
                course = None
                for _ in range(4):  # climb up a few levels; adjust if needed
                    container = container.parent
                    if not container:
                        break
                    name_tag = container.select_one("h4 a, h4, h3.courseName, .student-course-name, .courseName")
                    if name_tag:
                        course = name_tag.get_text(strip=True)
                        break
                if not course:
                    # As last resort, look left in the same row
                    sibling_name = fg.find_previous(["h3", "h4", "td", "th"])
                    if sibling_name:
                        course = sibling_name.get_text(strip=True)

                if not course:
                    continue

                pct, letter = extract_pct_and_letter(fg)
                if pct is not None:
                    results.append({course: pct})
                elif letter:
                    results.append({course: letter})

        # --- 4) Debug summary ---------------------------------------------------
        print(f"[GRADES] Parsed {len(results)} course grades.")
        if results[:3]:
            print("[GRADES] Sample:", results[:3])

        return results


    async def logout(self) -> None:
        """Log out of the Infinite Campus portal and close the page."""
        await self.page.goto(self.LOGOFF)
        await self.page.wait_for_timeout(500)
        await self.page.close()