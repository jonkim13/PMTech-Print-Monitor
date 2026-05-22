import os
import sqlite3
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.shared.sqlite_migrations import add_column_if_missing, has_column


def _new_conn(tmpdir):
    conn = sqlite3.connect(os.path.join(tmpdir, "t.db"))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    conn.commit()
    return conn


def _column_names(conn, table):
    return [row[1] for row in conn.execute(
        "PRAGMA table_info({})".format(table)).fetchall()]


class SqliteMigrationsTests(unittest.TestCase):
    def test_add_column_if_missing_adds_when_absent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _new_conn(tmpdir)
            self.assertFalse(has_column(conn, "t", "extra"))

            add_column_if_missing(conn, "t", "extra", "TEXT")

            self.assertIn("extra", _column_names(conn, "t"))
            self.assertTrue(has_column(conn, "t", "extra"))
            conn.close()

    def test_add_column_if_missing_is_idempotent_when_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _new_conn(tmpdir)
            add_column_if_missing(conn, "t", "extra", "TEXT")
            add_column_if_missing(conn, "t", "extra", "TEXT")

            cols = _column_names(conn, "t")
            self.assertEqual(cols.count("extra"), 1)
            conn.close()


if __name__ == "__main__":
    unittest.main()
