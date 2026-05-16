import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine


def _connection_url() -> str:
    # Keep env access lazy to avoid import-time failures in non-DB workflows/tests.
    host = os.getenv("PGHOST", "localhost")
    database = os.getenv("PGDATABASE", "")
    user = quote_plus(os.getenv("PGUSER", ""))
    password = quote_plus(os.getenv("PGPASSWORD", ""))
    port = os.getenv("PGPORT")

    port_segment = f":{port}" if port else ""
    return f"postgresql+psycopg://{user}:{password}@{host}{port_segment}/{database}?sslmode=require"


_ENGINE: Engine | None = None


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(_connection_url(), pool_pre_ping=True)
    return _ENGINE


def get_connection() -> Connection:
    return get_engine().connect()

