"""
db.py
------------------------------------------------------------------
Database-connectie voor J-Poule op een gratis PythonAnywhere-account:
SQLite in plaats van PostgreSQL (geen aparte databaseserver nodig,
werkt zonder de outbound-internetbeperkingen van het gratis plan).

Env var: DB_PATH (standaard: jpoule.db naast dit bestand)
------------------------------------------------------------------
"""

import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "jpoule.db"))


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # betere gelijktijdigheid bij meerdere workers
    return conn


@contextmanager
def get_connection():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(schema_path: str | None = None) -> None:
    """Eenmalig aan te roepen om een verse database te initialiseren."""
    schema_path = schema_path or os.path.join(os.path.dirname(__file__), "schema_sqlite.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        script = f.read()
    with get_connection() as conn:
        conn.executescript(script)

