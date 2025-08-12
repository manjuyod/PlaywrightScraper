@echo off
echo [1/3] Running Scraper...
uv run -m scraper.runner --franshise_id 57

echo.
echo [2/3] Updating db...
uv run -m scraper.work_flows.insert_grades

echo.
echo [3/3] Uploading to sheets...
uv run -m scraper.work_flows.update_sheets

echo.
echo Pipeline finished successfully.
pause
