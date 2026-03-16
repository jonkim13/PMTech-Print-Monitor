"""
Database Models
================
SQLite-backed databases for print history and filament inventory.
"""
import sqlite3
from datetime import datetime, timezone

# ============================================================
# PRINT HISTORY DATABASE
# ============================================================
class PrintHistoryDB:
    """SQLite-backed print history log."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS print_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                printer_id TEXT NOT NULL,
                printer_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                filename TEXT,
                from_status TEXT,
                to_status TEXT,
                duration_sec INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def log_event(self, event: dict):
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO print_history
                (timestamp, printer_id, printer_name, event_type,
                 filename, from_status, to_status, duration_sec)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event.get("timestamp", datetime.now(timezone.utc).isoformat()),
            event.get("printer_id", ""),
            event.get("printer_name", ""),
            event.get("type", "unknown"),
            event.get("filename", ""),
            event.get("from_status", ""),
            event.get("to_status", ""),
            event.get("duration_sec", 0),
        ))
        conn.commit()
        conn.close()

    def get_history(self, limit: int = 100) -> list:
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT * FROM print_history
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        conn = self._get_conn()
        total = conn.execute(
            "SELECT COUNT(*) FROM print_history"
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT COUNT(*) FROM print_history WHERE event_type='print_complete'"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM print_history WHERE event_type='printer_error'"
        ).fetchone()[0]
        started = conn.execute(
            "SELECT COUNT(*) FROM print_history WHERE event_type='print_started'"
        ).fetchone()[0]

        # Per-printer stats
        per_printer_rows = conn.execute("""
            SELECT printer_name, COUNT(*) as count
            FROM print_history
            WHERE event_type = 'print_complete'
            GROUP BY printer_name
        """).fetchall()
        per_printer = {r["printer_name"]: r["count"] for r in per_printer_rows}

        # Average duration of completed prints
        avg_row = conn.execute("""
            SELECT AVG(duration_sec) as avg_dur
            FROM print_history
            WHERE event_type = 'print_complete' AND duration_sec > 0
        """).fetchone()
        avg_duration = avg_row["avg_dur"] if avg_row["avg_dur"] else 0

        conn.close()

        success_rate = 0
        if completed + failed > 0:
            success_rate = round(completed / (completed + failed) * 100, 1)

        return {
            "total_events": total,
            "completed": completed,
            "failed": failed,
            "started": started,
            "success_rate": success_rate,
            "per_printer": per_printer,
            "avg_duration_sec": round(avg_duration),
        }


# ============================================================
# FILAMENT INVENTORY DATABASE
# ============================================================
class FilamentInventoryDB:
    """Read/write access to the FilamentInventory.db."""

    MATERIALS = {
        "PLA": "PLA", "ABS": "ABS", "ASA": "ASA", "PETG": "PEG",
        "Nylon": "NYL", "Nylon CF": "NYC", "PEEK": "PEK", "PEKK": "PKK",
        "ULTEM 1010": "U10", "ULTEM 9085": "U85", "TPU": "TPU", "SEBS": "SEB",
    }

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_table()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self):
        """Create the Filament table if it doesn't exist."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS Filament (
                id TEXT PRIMARY KEY,
                material TEXT NOT NULL,
                brand TEXT NOT NULL,
                color TEXT NOT NULL,
                supplier TEXT NOT NULL,
                grams INTEGER NOT NULL,
                diameter FLOAT NOT NULL,
                batch TEXT,
                operator TEXT NOT NULL,
                date_ins TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def get_all(self, material: str = None, brand: str = None,
                color: str = None, supplier: str = None) -> list:
        conn = self._get_conn()
        query = "SELECT * FROM Filament WHERE 1=1"
        params = []
        if material:
            query += " AND material = ?"
            params.append(material)
        if brand:
            query += " AND brand = ?"
            params.append(brand)
        if color:
            query += " AND color LIKE ?"
            params.append(f"%{color}%")
        if supplier:
            query += " AND supplier = ?"
            params.append(supplier)
        query += " ORDER BY date_ins DESC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_by_id(self, spool_id: str) -> dict:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM Filament WHERE id = ?", (spool_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def add_filament(self, material: str, brand: str, color: str,
                     supplier: str, grams: int, diameter: float,
                     batch: str, operator: str) -> str:
        """Add a new filament spool and return its generated ID."""
        conn = self._get_conn()
        # Generate ID: YY + material_code + sequence_number
        count = conn.execute(
            "SELECT COUNT(*) FROM Filament WHERE material = ?",
            (material,)
        ).fetchone()[0]
        seq = str(count + 1).zfill(3)
        year = datetime.now().strftime("%y")
        mat_code = self.MATERIALS.get(material, material[:3].upper())
        spool_id = f"{year}{mat_code}{seq}"
        date_ins = datetime.now().strftime("%Y-%m-%d")

        conn.execute("""
            INSERT INTO Filament
                (id, material, brand, color, supplier, grams,
                 diameter, batch, operator, date_ins)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (spool_id, material, brand, color, supplier, grams,
              diameter, batch, operator, date_ins))
        conn.commit()
        conn.close()
        return spool_id

    def update_weight(self, spool_id: str, new_grams: int) -> bool:
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE Filament SET grams = ? WHERE id = ?",
            (new_grams, spool_id)
        )
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    def delete_spool(self, spool_id: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM Filament WHERE id = ?", (spool_id,)
        )
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    def get_materials_list(self) -> list:
        return list(self.MATERIALS.keys())

    def get_brands_list(self) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT brand FROM Filament ORDER BY brand"
        ).fetchall()
        conn.close()
        return [r["brand"] for r in rows]

    def get_suppliers_list(self) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT supplier FROM Filament ORDER BY supplier"
        ).fetchall()
        conn.close()
        return [r["supplier"] for r in rows]

    def deduct_weight(self, spool_id: str, grams_used: int) -> bool:
        """Subtract grams_used from a spool, flooring at 0."""
        conn = self._get_conn()
        cursor = conn.execute("""
            UPDATE Filament
            SET grams = MAX(0, grams - ?)
            WHERE id = ?
        """, (grams_used, spool_id))
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed


# ============================================================
# FILAMENT ASSIGNMENTS (printer ↔ spool)
# ============================================================
class FilamentAssignmentDB:
    """Tracks which filament spool is loaded on which printer."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS filament_assignments (
                printer_id TEXT PRIMARY KEY,
                spool_id TEXT NOT NULL,
                assigned_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def assign(self, printer_id: str, spool_id: str) -> None:
        """Assign a spool to a printer (replaces any existing assignment)."""
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO filament_assignments
                (printer_id, spool_id, assigned_at)
            VALUES (?, ?, ?)
        """, (printer_id, spool_id,
              datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()

    def unassign(self, printer_id: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM filament_assignments WHERE printer_id = ?",
            (printer_id,)
        )
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    def get_assignment(self, printer_id: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM filament_assignments WHERE printer_id = ?",
            (printer_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_all_assignments(self) -> dict:
        """Return {printer_id: spool_id} for all assignments."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT printer_id, spool_id FROM filament_assignments"
        ).fetchall()
        conn.close()
        return {r["printer_id"]: r["spool_id"] for r in rows}
