# scraper/workflows/scrape_one.py
from __future__ import annotations

from typing import Dict, Any
from playwright.async_api import Page

from ..portals import get_portal   # registry helper


async def scrape_one_student(page: Page, student: Dict[str, str]) -> Dict[str, Any]:
    """
    Orchestrate the full scrape for a single student.

    Parameters
    ----------
    page     : Playwright Page already created by the caller
    student  : Dict with keys
               { "id": "...", "password": "...", "portal": "infinite_campus" }

    Returns
    -------
    dict     : { "id": <student id>, "grades": [ {course, score}, ... ] }
    """
    # 1. Dynamically pick the correct portal engine
    Engine = get_portal(student["portal"])

    # 2. Instantiate and run it
    scraper = Engine(page, student["id"], student["password"])
    await scraper.login()
    grades = await scraper.fetch_grades()

    # 3. Normalise the return value for the runner / DB layer
    return {"id": student["id"], "grades": grades}
