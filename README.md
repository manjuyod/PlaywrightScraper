# Student Grade Checker
Bundle of Engines for individual grade portals, providing sign-in and grade checking service

# Test Suite
Comparison of Sheet to DB
- uv run pytest -q
- uv run pytest -q --run-integration
- $env:TEST_FRANCHISE_ID=19; uv run pytest -q --run-integration

# Debug Run
- uv run -m scraper.work_flows.update_students --franchise-id 19 --debug
## TODO:
[\_] Consistent Logging \
[\_] Add Portals \
[\_] Propagate errors through RetryError \