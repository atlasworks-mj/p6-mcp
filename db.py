import os
from decimal import Decimal

import pyodbc
from dotenv import load_dotenv

load_dotenv()


def _build_connection_string() -> str:
    required = {"P6_SERVER", "P6_DATABASE", "P6_USER", "P6_PASSWORD"}
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(sorted(missing))}. "
            f"Copy .env.example to .env and fill in your P6 connection details."
        )
    return (
        f"DRIVER={{{os.getenv('P6_DRIVER', 'ODBC Driver 18 for SQL Server')}}};"
        f"SERVER={os.getenv('P6_SERVER')};"
        f"DATABASE={os.getenv('P6_DATABASE')};"
        f"UID={os.getenv('P6_USER')};"
        f"PWD={os.getenv('P6_PASSWORD')};"
        f"TrustServerCertificate=yes;"
    )


def get_connection():
    try:
        return pyodbc.connect(_build_connection_string())
    except pyodbc.Error:
        raise RuntimeError("Failed to connect to P6 database. Check your .env settings.")


def _rows_to_dicts(columns: list[str], rows) -> list[dict]:
    return [
        {k: float(v) if isinstance(v, Decimal) else v for k, v in zip(columns, row)}
        for row in rows
    ]


def query(sql: str, params: tuple = (), max_rows: int | None = None) -> list[dict]:
    """Execute a read-only query and return results as a list of dicts."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        if cursor.description is None:
            return []
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchmany(max_rows) if max_rows is not None else cursor.fetchall()
        return _rows_to_dicts(columns, rows)
    finally:
        conn.close()


def query_single(sql: str, params: tuple = ()) -> dict | None:
    """Execute a query and return the first result, or None."""
    results = query(sql, params)
    return results[0] if results else None
