"""To be used as a final test for each portal currently being managed"""

from scraper.runner import students as get_students, scrape_one


async def test_portal(pw, portal) -> bool:
    try:
        students = get_students(portal=portal)
        import random
        student = random.choice(students)
        await scrape_one(pw, student)
        return True
    except:
        return False


from playwright.async_api import async_playwright

async def full_test() -> dict[str, bool]:
    portals = managed_portals.keys()
    output = {portal: False for portal in portals}
    async with async_playwright() as pw:
        for portal in portals:
            print(f"Testing portal: {portal}")
            output[portal] = await test_portal(pw, portal)
    return output


from scraper.portals import managed_portals
import asyncio

if __name__ == '__main__':
    results = asyncio.run(full_test())

    print(results)
    print('====[Portal Test Results]====')
    for test_portal, passed in results.items():
        result = 'PASSED' if passed else 'FAILED'
        print(f"{test_portal} - {result}")
    print('=============================')
