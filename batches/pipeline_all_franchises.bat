@echo off
call "%~dp0_bootstrap.bat" || exit /b 1

echo Running pipeline for all configured franchises...

setlocal enableextensions
set "FAILED="

for %%F in (6 8 16 11 15 57 19 49 60 74 87) do (
  echo.
  echo ===== Franchise %%F =====
  call "%~dp0pipeline_franchise.bat" %%F
  if errorlevel 1 (
    echo [ERROR] pipeline failed for franchise %%F
    set "FAILED=1"
  )
)

echo.
if defined FAILED (
  echo Pipeline finished with errors.
  exit /b 1
)

echo Pipeline finished successfully.
exit /b 0
