"""
SQLite storage layer for watchlist and catalyst data.
Database path is configurable via DB_PATH environment variable.
"""

import os
import sqlite3
from typing import Any, Dict, Optional

DB_PATH = os.environ.get("DB_PATH", "data/biotech.db")


def _get_conn() -> sqlite3.Connection:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                ticker TEXT PRIMARY KEY,
                note   TEXT NOT NULL DEFAULT '',
                added  TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS catalysts (
                ticker              TEXT PRIMARY KEY,
                next_catalyst_date  TEXT NOT NULL DEFAULT '',
                catalyst_label      TEXT NOT NULL DEFAULT '',
                notes               TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.commit()


# ─── Watchlist ────────────────────────────────────────────────────────────────

def get_watchlist() -> Dict[str, Any]:
    init_db()
    with _get_conn() as conn:
        rows = conn.execute("SELECT ticker, note, added FROM watchlist").fetchall()
    return {row["ticker"]: {"note": row["note"], "added": row["added"]} for row in rows}


def save_to_watchlist(ticker: str, note: str = "", added: str = "") -> None:
    init_db()
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO watchlist (ticker, note, added) VALUES (?, ?, ?)",
            (ticker.upper(), note, added),
        )
        conn.commit()


def remove_from_watchlist(ticker: str) -> None:
    init_db()
    with _get_conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker.upper(),))
        conn.commit()


# ─── Catalysts ────────────────────────────────────────────────────────────────

def get_catalysts(ticker: Optional[str] = None) -> Dict[str, Any]:
    init_db()
    with _get_conn() as conn:
        if ticker:
            rows = conn.execute(
                "SELECT ticker, next_catalyst_date, catalyst_label, notes "
                "FROM catalysts WHERE ticker = ?",
                (ticker.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ticker, next_catalyst_date, catalyst_label, notes FROM catalysts"
            ).fetchall()
    return {
        row["ticker"]: {
            "next_catalyst_date": row["next_catalyst_date"],
            "catalyst_label": row["catalyst_label"],
            "notes": row["notes"],
        }
        for row in rows
    }


def save_catalyst(
    ticker: str,
    next_catalyst_date: str = "",
    catalyst_label: str = "",
    notes: str = "",
) -> None:
    init_db()
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO catalysts "
            "(ticker, next_catalyst_date, catalyst_label, notes) VALUES (?, ?, ?, ?)",
            (ticker.upper(), next_catalyst_date, catalyst_label, notes),
        )
        conn.commit()


def delete_catalyst(ticker: str) -> None:
    init_db()
    with _get_conn() as conn:
        conn.execute("DELETE FROM catalysts WHERE ticker = ?", (ticker.upper(),))
        conn.commit()
