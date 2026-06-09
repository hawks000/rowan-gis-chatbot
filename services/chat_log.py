"""SQLite persistence for chat query history."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

DEFAULT_DB_PATH = os.path.join("data", "chatbot.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chat_history (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at    TEXT NOT NULL,
  session_id    TEXT NOT NULL,
  user_message  TEXT NOT NULL,
  parse_method  TEXT NOT NULL,
  intent_type   TEXT,
  intent_json   TEXT,
  status        TEXT NOT NULL,
  result_count  INTEGER DEFAULT 0,
  layer_used    TEXT,
  error_message TEXT,
  response_ms   INTEGER,
  needs_feature INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chat_created ON chat_history(created_at);
CREATE INDEX IF NOT EXISTS idx_chat_status ON chat_history(status);
CREATE INDEX IF NOT EXISTS idx_chat_intent ON chat_history(intent_type);
"""


def get_db_path() -> str:
    return os.getenv("CHAT_DB_PATH", DEFAULT_DB_PATH)


@contextmanager
def get_connection(db_path: str | None = None):
    path = db_path or get_db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


def log_query(
    *,
    session_id: str,
    user_message: str,
    parse_method: str,
    intent_type: str | None,
    intent: dict[str, Any] | None,
    status: str,
    result_count: int = 0,
    layer_used: str | None = None,
    error_message: str | None = None,
    response_ms: int | None = None,
) -> int:
    init_db()
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO chat_history (
              created_at, session_id, user_message, parse_method, intent_type,
              intent_json, status, result_count, layer_used, error_message, response_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                session_id,
                user_message,
                parse_method,
                intent_type,
                json.dumps(intent) if intent else None,
                status,
                result_count,
                layer_used,
                error_message,
                response_ms,
            ),
        )
        return int(cursor.lastrowid)


def list_queries(
    *,
    status: str | None = None,
    unmatched_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    init_db()
    clauses: list[str] = []
    params: list[Any] = []

    if status:
        clauses.append("status = ?")
        params.append(status)
    if unmatched_only:
        clauses.append("(status IN ('parse_failed', 'no_match') OR intent_type IS NULL)")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM chat_history
            {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def get_summary_stats() -> dict[str, Any]:
    init_db()
    with get_connection() as conn:
        totals = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
              SUM(CASE WHEN status IN ('parse_failed', 'no_match') THEN 1 ELSE 0 END) AS unmatched_count
            FROM chat_history
            """
        ).fetchone()
        top_intents = conn.execute(
            """
            SELECT intent_type, COUNT(*) AS cnt
            FROM chat_history
            WHERE intent_type IS NOT NULL
            GROUP BY intent_type
            ORDER BY cnt DESC
            LIMIT 10
            """
        ).fetchall()
    return {
        "total": totals["total"] or 0,
        "success_count": totals["success_count"] or 0,
        "unmatched_count": totals["unmatched_count"] or 0,
        "top_intents": [dict(row) for row in top_intents],
    }


def set_needs_feature(entry_id: int, needs_feature: bool) -> bool:
    init_db()
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE chat_history SET needs_feature = ? WHERE id = ?",
            (1 if needs_feature else 0, entry_id),
        )
        return cursor.rowcount > 0
