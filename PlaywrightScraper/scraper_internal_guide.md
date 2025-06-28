# Student-Grade Scraper — Internal Developer Guide
*(Playwright + Python 3.11)*  

This document explains the current architecture, key modules, and day-to-day workflows so new team members can contribute quickly.

---

## 1 · Repository Layout
```
PlaywrightScraper/
├── config/
│   └── students.toml #currently set-up to credenitals and portal key per student. However in the future I'd like this to be pointed to the db
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
1. `runner.py` → reads TOML, spawns async tasks  
2. `scrape_one()` → jitter, semaphore, login, scrape, handle CAPTCHA  
3. Portal engine → site‑specific login + grade parse  

---

## 3 · Key Components
* **utils/ratelimiter.py** – token bucket, `global_limiter.acquire()` # not fleshed out yet
* **utils/captcha_guard.py** – `ensure_not_captcha(page)` raises `CaptchaError`  # not fleshed out yet
* **scraper/portals/base.py** – `PortalEngine` ABC  
* **Tenacity** – retries everything except `CaptchaError`

---

## 4 · Configuration
```toml
[[student]]
id       = "alice123"
password = "secret"
portal   = "infinite_campus_student_ccsd"
```
ENV flags: `DEBUG`, `OUTPUT_DIR`.

---

## 5 · Run
```bash
uv venv #if you need to hard copy this folder into you local environment
uv sync #all to manage the dependencies
uv run playwright install        # first time
uv run python -m scraper.runner  # headless
DEBUG=true uv run python -m scraper.runner
```

---

## 6 · Add a Portal --- This is the current stage we're at.
1. Create `scraper/portals/<name>.py`  
2. Decorate `@register_portal("key")`  
3. Import in `portals/__init__.py`  
4. Add semaphore in runner  
5. Update `students.toml`

---

## 7 · Troubleshooting
| Symptom                       | Fix                                                                            |
| ----------------------------- | ------------------------------------------------------------------------------ |
| `ModuleNotFoundError: utils`  | Ensure `utils/__init__.py` exists **and** folder sits at project root.         |
| `dict object is not callable` | Renamed a Tenacity function; don’t shadow `retry`, etc.                        |
| CAPTCHA png saved             | Check `captcha.png`; if legit, re-queue student or build solver.               |
| Rate-limit 429                | Lower `global_limiter` rate or per-portal semaphore; verify Tenacity back-off. |
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
