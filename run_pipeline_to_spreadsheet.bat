@echo off
echo [1/4] Running Update...
uv run -m scraper.work_flows.update_students

echo [2/4] Running Scraper...
uv run -m scraper.runner

echo.
echo [3/4] Updating db...
uv run -m scraper.work_flows.insert_grades

echo.
echo [4/4] Uploading to sheets...
uv run -m scraper.work_flows.update_sheets

echo.
echo Pipeline finished successfully.
