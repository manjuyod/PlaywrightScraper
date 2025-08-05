@echo off
echo [1/3] Running Scraper...
uv run python -m scraper.runner

echo.
echo [2/3] Processing Raw Grade Data...
uv run python -m scraper.post_processing

echo.
echo [3/3] Converting Report to Excel...
uv run python -m scraper.to_excel

echo.
echo Pipeline finished successfully.
pause
