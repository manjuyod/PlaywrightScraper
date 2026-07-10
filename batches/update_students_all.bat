@echo off
call "%~dp0_bootstrap.bat" || exit /b 1

echo Reconciling canonical students through the private API...
uv run python scripts\windows_pipeline.py --reconcile
exit /b %errorlevel%
