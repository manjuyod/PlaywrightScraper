@echo off
call "%~dp0_bootstrap.bat" || exit /b 1

echo Running one API-only reconciliation, enqueue, and drain pass...
uv run python scripts\windows_pipeline.py
if errorlevel 1 exit /b 1

echo Pipeline finished successfully.
exit /b 0
