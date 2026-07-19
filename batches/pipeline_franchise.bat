@echo off
call "%~dp0_bootstrap.bat" || exit /b 1

set "FID=%~1"
if "%FID%"=="" (
  echo Usage: %~nx0 ^<franchise-id^>
  exit /b 2
)

echo(%FID%| findstr /r "^[0-9][0-9]*$" >nul
if errorlevel 1 (
  echo [ERROR] franchise-id must be an integer: "%FID%"
  exit /b 2
)

echo Running Rust-backed scraper (FranchiseID=%FID%)...
uv run -m scraper.runner --franchise-id %FID%
if errorlevel 1 exit /b 1

echo.
echo Pipeline finished successfully (FranchiseID=%FID%).
exit /b 0

