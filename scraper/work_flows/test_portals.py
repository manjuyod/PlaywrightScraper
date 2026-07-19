"""To be used as a final test for each portal currently being managed"""

import argparse
import asyncio
import random
from time import time

from scraper.runner import students as get_students, scrape_one
from db import filter_group
from playwright.async_api import async_playwright
from scraper.portals import managed_portals

async def test_portal(browser, portal, student: dict | None = None) -> bool:
    print(portal)
    if student is None:
        _students = filter_group(students, 'portal', portal)
        student = random.choice(_students) if len(_students) > 0 else None
        print(f"Selected CRM student {student.get('db_id') if student else 'none'}")
        if student is None:
            return True
    try:
        await scrape_one(browser, student)
        return True
    except Exception as e:
        print(type(e).__name__, flush=True)
        return False


async def batch_portal_test(browser, portal) -> bool:
    _students = filter_group(students, 'portal', portal)
    num_to_sample = min(5, len(_students))
    _students = random.sample(_students, num_to_sample)

    # ASYNC
    tasks = [test_portal(browser, portal, student) for student in _students]
    results = await asyncio.gather(*tasks)

    # SYNC
    # results = []
    # for student in _students:
    #     results.append(await test_portal(pw, portal, student))

    return False not in results


async def full_test(pw) -> dict[str, bool]:
    portals = managed_portals.keys()
    output = {portal: False for portal in portals}
    tasks = [test_portal(pw, portal) for portal in portals]
    results = await asyncio.gather(*tasks)
    print(results)
    for (portal, result) in zip(portals, results):
        output[portal] = result
    return output
async def main(portal: str | None):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        start_time = time()
        if portal: # one portal
            result = await batch_portal_test(browser, portal)
            print(f"[test] {args.portal}")
            print(f"[test] Success? {result}")
        else: # all portals
            results = await full_test(browser)
            print('====[Portal Test Results]====')
            for portal, passed in results.items():
                result = 'PASSED' if passed else 'FAILED'
                print(f"{portal} |  {result}")
            print('=============================')
    end_time = time()

    seconds_elapsed = end_time - start_time
    print(f"The process took {seconds_elapsed} seconds.")
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Test portions of the grade checker.")
    parser.add_argument(
        "-p", "--portal",
        type=str,
        help="Test a batch of students from a single portal by name."
    )
    args = parser.parse_args()

    students = get_students()
    asyncio.run(main(args.portal))

    print("[test] CLI args:", args, flush=True)







    # print(results)

