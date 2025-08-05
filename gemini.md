# Gemini Project Knowledge Base

This document summarizes the key findings and configurations for the PlaywrightScraper project.

## Key Learnings & Fixes

1.  **Critical Timing Issue:** The primary cause of scraper failures was a subtle timing issue on the Infinite Campus website. The gradebook page appears to load necessary components *after* the `domcontentloaded` event, which caused Playwright's smart waits to time out.
    *   **Solution:** A hard 3-second pause (`await self.page.wait_for_timeout(3000)`) was added immediately after navigating to the gradebook page. This stabilized the scraping process.

2.  **Robust Logging:** The scraper was modified to be more resilient. Instead of gathering all results in memory, it now writes each result to a `grades.jsonl` file as it completes using `asyncio.as_completed`.

3.  **Data Processing Pipeline:** A multi-stage data processing pipeline was created to transform the raw scraped data into a stakeholder-ready Excel report. Each stage has a dedicated script and can be run independently or as part of a full sequence. The intermediate files are automatically deleted upon successful transformation by the next stage.
    *   **Stage 1 (Raw JSONL):** `scraper/runner.py` -> `output/phase1totuples/grades.jsonl`
    *   **Stage 2 (Processed JSON):** `scraper/post_processing.py` -> `output/phase2todf/grades_report.json`
    *   **Stage 3 (Excel Report):** `scraper/to_excel.py` -> `output/phase22toexcelfortest/grade_test.xlsx`

## Project Commands

### Full Pipeline (Recommended)
To run the entire process from scraping to the final Excel report, use the provided batch file.

```sh
.\run_full_pipeline.bat
```

### Individual Steps
You can also run each step of the pipeline individually.

1.  **Run the Scraper:**
    ```sh
    # Run for all active students
    uv run python -m scraper.runner

    # Or run for a specific franchise
    uv run python -m scraper.runner --franchise-id 57
    ```
2.  **Process Raw Data:**
    ```sh
    uv run python -m scraper.post_processing
    ```
3.  **Generate Excel Report:**
    ```sh
    uv run python -m scraper.to_excel
    ```

### Debugging
To run in non-headless (visible) mode for debugging, the `DEBUG` environment variable must be set to `true`.

*   **PowerShell:** `$env:DEBUG="true"; uv run -m scraper.runner`
*   **CMD:** `set DEBUG=true&&uv run -m scraper.runner`
