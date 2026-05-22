"""Shared SQLite column-level migration helpers.

Replaces the per-repository `_has_column` / `_add_column_if_missing`
copies. The canonical shape matches `WorkOrderRepository`'s prior
static-method versions: `add_column_if_missing` returns None and
commits inline; the function is idempotent.
"""
import sqlite3


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cursor = conn.execute("PRAGMA table_info({})".format(table))
    columns = [row[1] for row in cursor.fetchall()]
    return column in columns


def add_column_if_missing(conn: sqlite3.Connection, table: str,
                          column: str, column_def: str) -> None:
    if has_column(conn, table, column):
        return
    conn.execute(
        "ALTER TABLE {} ADD COLUMN {} {}".format(table, column, column_def)
    )
    conn.commit()
