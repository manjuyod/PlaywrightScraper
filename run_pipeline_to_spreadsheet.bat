@echo off

rem Always run from repo root (Task Scheduler often starts in System32).
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo [ERROR] "uv" not found on PATH for this user.
  exit /b 1
)

echo [1/4] Running Update...
uv run -m scraper.work_flows.update_students
if errorlevel 1 exit /b 1

echo [2/4] Running Scraper...
uv run -m scraper.runner
if errorlevel 1 exit /b 1

echo.
echo [3/4] Updating db...
uv run -m scraper.work_flows.insert_grades
if errorlevel 1 exit /b 1

echo.
echo [4/4] Uploading to sheets...
uv run -m scraper.work_flows.update_sheets
if errorlevel 1 exit /b 1

echo.
echo Pipeline finished successfully.
exit /b 0
