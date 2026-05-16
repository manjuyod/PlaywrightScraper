import os
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine


GRADES_NEON_COMPONENTS = (
    "GRADES_NEON_HOST",
    "GRADES_NEON_DB",
    "GRADES_NEON_USER",
    "GRADES_NEON_PASSWORD",
)


def _with_psycopg_driver(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgresql+psycopg://"):
        return url
    raise ValueError(
        "GRADES_NEON_URL must start with postgres://, postgresql://, "
        "or postgresql+psycopg://."
    )


def _require_sslmode(url: str) -> str:
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "sslmode"]
    query.append(("sslmode", "require"))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _connection_url() -> str:
    # Keep env access lazy to avoid import-time failures in non-DB workflows/tests.
    url = os.getenv("GRADES_NEON_URL", "").strip()
    if url:
        return _require_sslmode(_with_psycopg_driver(url))

    missing = [
        name for name in GRADES_NEON_COMPONENTS if not os.getenv(name, "").strip()
    ]
    if missing:
        raise ValueError(
            "Missing required Neon database environment variables: "
            + ", ".join(missing)
            + ". Set GRADES_NEON_URL or all GRADES_NEON_* connection components."
        )

    host = os.getenv("GRADES_NEON_HOST", "").strip()
    database = os.getenv("GRADES_NEON_DB", "").strip()
    user = quote_plus(os.getenv("GRADES_NEON_USER", "").strip())
    password = quote_plus(os.getenv("GRADES_NEON_PASSWORD", "").strip())
    port = os.getenv("GRADES_NEON_PORT", "").strip()

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

