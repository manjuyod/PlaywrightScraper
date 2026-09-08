import os
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine


def _env_value(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _normalize_postgres_url(raw_url: str) -> str:
    url = raw_url.strip()
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")

    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if not any(key == "sslmode" for key, _ in query):
        query.append(("sslmode", "require"))
    return urlunsplit(parsed._replace(query=urlencode(query)))


def _connection_url() -> str:
    # Keep env access lazy to avoid import-time failures in non-DB workflows/tests.
    database_url = os.getenv("GRADES_NEON_URL")
    if database_url:
        return _normalize_postgres_url(database_url)

    host = _env_value("PGHOST", "GRADES_NEON_HOST", default="localhost")
    database = _env_value("PGDATABASE", "GRADES_NEON_DB", "GRADES_NEON_DATABASE")
    user = quote_plus(_env_value("PGUSER", "GRADES_NEON_USER"))
    password = quote_plus(_env_value("PGPASSWORD", "GRADES_NEON_PASSWORD"))
    port = _env_value("PGPORT", "GRADES_NEON_PORT")

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

