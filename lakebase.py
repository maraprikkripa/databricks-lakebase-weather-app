"""
Lakebase (Databricks-managed Postgres) connection helper.

Connects using a single LAKEBASE_URL secret (a standard Postgres connection URL)
pointing at a native Postgres role with a static, non-expiring password.
"""

import base64
import os
from contextlib import contextmanager

import pg8000
from databricks.sdk import WorkspaceClient

_w = WorkspaceClient()

_SCOPE = os.environ.get("LAKEBASE_SECRET_SCOPE", "database")
_KEY = os.environ.get("LAKEBASE_SECRET_KEY", "lakebase-url")


def _lakebase_url() -> str:
    """Fetch and decode the Lakebase connection URL from the Databricks secret scope."""
    secret = _w.secrets.get_secret(scope=_SCOPE, key=_KEY)
    # The secret is base64-encoded once - decode to get the PostgreSQL URL
    decoded = base64.b64decode(secret.value).decode("utf-8")
    return decoded


def _parse_connection_url(url: str) -> dict:
    """Parse PostgreSQL connection URL into pg8000 connection parameters."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip('/'),
        "user": parsed.username,
        "password": parsed.password,
        "ssl_context": True  # Enable SSL
    }


@contextmanager
def get_connection():
    """Yield a pg8000 connection (pure Python, Serverless-compatible)."""
    params = _parse_connection_url(_lakebase_url())
    conn = pg8000.connect(**params)
    try:
        yield conn
    finally:
        conn.close()


def run_query(sql: str, params: tuple | list | None = None) -> list[dict]:
    """Run a read query against Lakebase and return rows as list[dict]."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params or [])
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]


def run_write(sql: str, params: tuple | list | None = None) -> int:
    """Run an INSERT/UPDATE/DELETE against Lakebase, return affected row count."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params or [])
        conn.commit()
        return cursor.rowcount
