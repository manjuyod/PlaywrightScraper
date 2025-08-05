# Student-Grade Scraper — Internal Developer Guide
*(Playwright + Python 3.11)*  

This document explains the current architecture, key modules, and day-to-day workflows so new team members can contribute quickly.

---

## 1 · Repository Layout
```
PlaywrightScraper/
├── config/
│   └── students.db # SQLite database for student credentials
├── scraper/
│   ├── runner.py
│   ├── portals/
│   │   ├── __init__.py
│   │   └── infinite_campus_student_ccsd.py #Modular set-up. Analyze this and start building scraper workflows for each location.
│   └── work_flows/
│       └── scraper_one.py # Handles the actual scraping function
├── utils/
│   ├── ratelimiter.py
│   └── captcha_guard.py
└── pyproject.toml
```

---

## 2 · High-Level Flow
1. `runner.py` → reads `students.db`, iterates sequentially  
2. `scrape_one()` → login, scrape, handle CAPTCHA  
3. Portal engine → site‑specific login + grade parse  

---

## 3 · Key Components
* **utils/ratelimiter.py** – token bucket, `global_limiter.acquire()` # not fleshed out yet
* **utils/captcha_guard.py** – `ensure_not_captcha(page)` raises `CaptchaError`  # not fleshed out yet
* **scraper/portals/base.py** – `PortalEngine` ABC  
* **Tenacity** – retries everything except `CaptchaError`

---

## 4 · Configuration
Student credentials and portal information are stored in the `config/students.db` SQLite database. The `Student` table is queried by the runner.

---

## 5 · Run
The scraper automatically filters for students whose `YearStart` and `YearEnd` dates are active.

```bash
uv venv #if you need to hard copy this folder into you local environment
uv sync #all to manage the dependencies
uv run playwright install        # first time

# Run for all active students
uv run python -m scraper.runner

# Run for a specific franchise
uv run python -m scraper.runner --franchise-id 57
```

---

## 6 · Add a Portal --- This is the current stage we're at.
1. Create `scraper/portals/<name>.py`  
2. Decorate `@register_portal("key")`  
3. Import in `portals/__init__.py`  
4. Update the `portal` column for the relevant student(s) in `students.db`.

---

## 7 · Troubleshooting
| Symptom                       | Fix                                                                            |
| ----------------------------- | ------------------------------------------------------------------------------ |
| `ModuleNotFoundError: utils`  | Ensure `utils/__init__.py` exists **and** folder sits at project root.         |
| `dict object is not callable` | Renamed a Tenacity function; don’t shadow `retry`, etc.                        |
| CAPTCHA png saved             | Check `captcha.png`; if legit, re-queue student or build solver.               |
| Iframe never loads            | Portal layout changed → update `wait_for_function()` substring.                |


## 8 · Extending Parsing Logic
- For small portal-specific tweaks, keep BeautifulSoup logic inside the engine.
- For shared logic, move parser to scraper/parsers/ and import in multiple engines.
- If you'd like to work on utilites, make sure that they end up imported in the correct scripts.

## 9 · Pending Enhancements 
- Headless-stealth (navigator.webdriver=false, UA spoof)
- JSON processing to the next step (eventually "Subject": Percentage/score tuples)
- Dynamic automated api calls to each cetner's google sheets in order to update grades
- Unit tests: saved HTML fixtures → parser assertions
