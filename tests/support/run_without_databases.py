"""Run selected pytest tests with application database connectors disabled.

Inspect selected tests before using this runner. Subprocesses do not inherit
these patches and must independently use database-free fixtures/harnesses.
"""

from pathlib import Path
import sys
from unittest.mock import patch
from contextlib import ExitStack

import pytest


def database_access_forbidden(*args, **kwargs):
    raise AssertionError("Application database access is prohibited in this test run")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    with ExitStack() as patches:
        for target in (
            "pyodbc.connect",
            "psycopg.connect",
            "psycopg.Connection.connect",
            "psycopg.AsyncConnection.connect",
            "sqlalchemy.engine.Engine.connect",
        ):
            patches.enter_context(patch(target, database_access_forbidden))
        raise SystemExit(pytest.main(sys.argv[1:]))
