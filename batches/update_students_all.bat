@echo off
call "%~dp0_bootstrap.bat" || exit /b 1

echo [1/1] Running Update Students...

setlocal enableextensions
set "FAILED="

for %%F in (6 11 15 16 19 49 57 60 74 87) do (
  echo.
  echo ===== Franchise %%F =====
  uv run -m scraper.work_flows.update_students --franchise-id %%F
  if errorlevel 1 (
    echo [ERROR] update_students failed for franchise %%F
    set "FAILED=1"
  )
)

echo.
if defined FAILED (
  echo Update finished with errors.
  exit /b 1
)

echo Update finished successfully.
exit /b 0
