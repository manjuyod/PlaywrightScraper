@echo off
setlocal enableextensions

rem Always run from repo root (Task Scheduler often starts in System32).
cd /d "%~dp0"
cd /d ..

if not exist "pyproject.toml" (
  echo [ERROR] Repo root not found - missing pyproject.toml. Current dir: %CD%
  exit /b 1
)

where uv >nul 2>nul
if errorlevel 1 (
  echo [ERROR] "uv" not found on PATH for this user.
  echo         Install uv and/or add it to PATH for the scheduled task account.
  exit /b 1
)

if not exist ".env" (
  echo [WARN] ".env" not found in repo root: %CD%
)
exit /b 0
