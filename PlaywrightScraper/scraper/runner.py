# scraper/runner.py
import asyncio, tomllib, pathlib, json, sys, os, random
from playwright.async_api import async_playwright
from scraper.portals import get_portal
from traceback import format_exception_only

MAX_PARALLEL = 8
CONF = pathlib.Path(__file__).parent.parent / "config/students.toml"
DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"

# Per-portal semaphores to prevent hammering individual sites
portal_locks = {
    "infinite_campus_student_ccsd": asyncio.Semaphore(2),
    # Add other portals as needed
}

def students():
    return tomllib.loads(CONF.read_text())["student"]

async def scrape_one(pw, sem, student):
    # Random start jitter to prevent burst alignment
    await asyncio.sleep(random.uniform(0, 2.0))
    async with sem:
        # Portal-specific rate limiting
        portal_name = student.get("portal", "default")
        portal_sem = portal_locks.get(portal_name, asyncio.Semaphore(1))
        async with portal_sem:
            browser_args = [
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor'
            ]

            # In debug mode, run with visible browser and slower execution
            browser = await pw.chromium.launch(
                headless= not DEBUG_MODE,
                slow_mo=500 if DEBUG_MODE else 0,
                args=browser_args
            )
            context = await browser.new_context()
            page = await context.new_page()

            Engine = get_portal(student["portal"])
            scraper = Engine(page, student["id"], student["password"])

            try:
                if DEBUG_MODE:
                    print(f"DEBUG: Starting login for student {student['id']}")

                await scraper.login()

                if DEBUG_MODE:
                    print(f"DEBUG: Login successful for {student['id']}, fetching grades...")

                grades = await scraper.fetch_grades()

                if DEBUG_MODE:
                    if isinstance(grades, dict) and "raw_html" in grades:
                        size = len(grades["raw_html"])
                        print(f"DEBUG: Received grade HTML for {student['id']} ({size:,} chars)")
                    else:
                        print(f"DEBUG: Received {len(grades)} grade records for {student['id']}")

                # Handle both list and dict return types from portals
                if isinstance(grades, dict):
                    # Portal returns a dict (like Infinite Campus with raw_html)
                    if "raw_html" in grades:
                        output_dir = pathlib.Path("output/phase1tuples")
                        output_dir.mkdir(parents=True, exist_ok=True)
                        file_name = f"{student['id']}_grades.html"
                        html_file = output_dir / file_name
                        html_file.write_text(grades["raw_html"], encoding="utf-8")
                        grades["file"] = str(html_file)    # bookmark it in the JSON
                    
                    return {
                        "id": student["id"],
                        "grades": grades
                    }
                else:
                    # Portal returns a list of grade records
                    return {
                        "id": student["id"],
                        "grades": grades
                    }
            except Exception as e:
                if DEBUG_MODE:
                    print(f"DEBUG: Error for student {student['id']}: {str(e)}")
                    # Keep browser open for debugging if there's an error
                    input("Press Enter to continue after inspecting the browser...")
                raise e
            finally:
                if not DEBUG_MODE:
                    await browser.close()
                else:
                    print(f"DEBUG: Keeping browser open for {student['id']} - close manually")

async def main():
    sem = asyncio.Semaphore(MAX_PARALLEL)
    async with async_playwright() as p:
        tasks = [scrape_one(p, sem, s) for s in students()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Handle exceptions in results before JSON serialization
    processed_results = []
    for result in results:
        if isinstance(result, Exception):
            processed_results.append({
                "error": str(result),
                "error_type": type(result).__name__
            })
        else:
            processed_results.append(result)

    output_dir = pathlib.Path("output/phase1totuples")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "grades.json"
    output_file.write_text(json.dumps(processed_results, indent=2))
    print(f"Scraping complete! Results saved to {output_file}")
    print(f"Successfully processed {len([r for r in processed_results if 'error' not in r])} students")
    print(f"Errors encountered: {len([r for r in processed_results if 'error' in r])}")

if __name__ == "__main__":
    asyncio.run(main())