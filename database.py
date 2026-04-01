"""
Database Models
================
SQLite-backed databases for print history and filament inventory.
"""
import sqlite3
from datetime import datetime, timezone
from typing import Optional

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
        conn.execute("PRAGMA journal_mode=WAL")
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
    DEPRECATED_CREATION_MATERIALS = frozenset({"NylonCF", "PEKK"})
    ALLOWED_SUPPLIERS = (
        "Prusa Research",
        "3DXTech",
        "Printed Solid",
    )

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_table()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
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
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(Filament)")
        }
        if "last_dried_at" not in columns:
            conn.execute(
                "ALTER TABLE Filament ADD COLUMN last_dried_at TEXT"
            )
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
        if supplier not in self.ALLOWED_SUPPLIERS:
            allowed = ", ".join(self.ALLOWED_SUPPLIERS)
            raise ValueError(
                f"Invalid supplier '{supplier}'. Allowed suppliers: {allowed}"
            )

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

    def update_last_dried(self, spool_id: str,
                          last_dried_at: Optional[str] = None) -> bool:
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE Filament SET last_dried_at = ? WHERE id = ?",
            (
                last_dried_at or datetime.now(timezone.utc).isoformat(),
                spool_id,
            )
        )
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    def get_materials_list(self) -> list:
        return list(self.MATERIALS.keys())

    def get_filter_materials_list(self) -> list:
        materials = list(self.MATERIALS.keys())
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT DISTINCT material
            FROM Filament
            WHERE material IS NOT NULL AND material != ''
            ORDER BY material
        """).fetchall()
        conn.close()
        extra_materials = [
            r["material"] for r in rows if r["material"] not in materials
        ]
        return materials + extra_materials

    def get_creation_materials_list(self) -> list:
        return [
            material for material in self.MATERIALS
            if material not in self.DEPRECATED_CREATION_MATERIALS
        ]

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

    def deduct_weight(self, spool_id: str, grams_used: float) -> bool:
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
# FILAMENT ASSIGNMENTS (printer + tool ↔ spool)
# ============================================================
class FilamentAssignmentDB:
    """Tracks which filament spool is loaded on which printer tool.

    Each printer can have multiple tools (nozzles). The XL has up to 5
    (tool_index 0-4), while the Core One has 1 (tool_index 0).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        conn = self._get_conn()
        # Check if old schema (printer_id as sole PRIMARY KEY) exists
        # and migrate to new composite key schema
        cursor = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='filament_assignments'"
        )
        row = cursor.fetchone()
        if row and "tool_index" not in row["sql"]:
            # Migrate: add tool_index column to existing data
            conn.executescript("""
                ALTER TABLE filament_assignments
                    RENAME TO filament_assignments_old;
                CREATE TABLE filament_assignments (
                    printer_id TEXT NOT NULL,
                    tool_index INTEGER NOT NULL DEFAULT 0,
                    spool_id TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    PRIMARY KEY (printer_id, tool_index)
                );
                INSERT INTO filament_assignments
                    (printer_id, tool_index, spool_id, assigned_at)
                SELECT printer_id, 0, spool_id, assigned_at
                FROM filament_assignments_old;
                DROP TABLE filament_assignments_old;
            """)
            conn.commit()
        elif not row:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS filament_assignments (
                    printer_id TEXT NOT NULL,
                    tool_index INTEGER NOT NULL DEFAULT 0,
                    spool_id TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    PRIMARY KEY (printer_id, tool_index)
                )
            """)
            conn.commit()
        conn.close()

    def assign(self, printer_id: str, spool_id: str,
               tool_index: int = 0) -> None:
        """Assign a spool to a specific tool on a printer."""
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO filament_assignments
                (printer_id, tool_index, spool_id, assigned_at)
            VALUES (?, ?, ?, ?)
        """, (printer_id, tool_index, spool_id,
              datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()

    def unassign(self, printer_id: str, tool_index: int = 0) -> bool:
        """Remove the spool assignment for a specific tool."""
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM filament_assignments "
            "WHERE printer_id = ? AND tool_index = ?",
            (printer_id, tool_index)
        )
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    def unassign_all(self, printer_id: str) -> bool:
        """Remove all spool assignments for a printer."""
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM filament_assignments WHERE printer_id = ?",
            (printer_id,)
        )
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    def get_assignment(self, printer_id: str,
                       tool_index: int = 0) -> Optional[dict]:
        """Get the assignment for a specific tool on a printer."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM filament_assignments "
            "WHERE printer_id = ? AND tool_index = ?",
            (printer_id, tool_index)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_printer_assignments(self, printer_id: str) -> list:
        """Get all tool assignments for a printer, ordered by tool_index."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM filament_assignments "
            "WHERE printer_id = ? ORDER BY tool_index",
            (printer_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_spool_assignments(self, spool_id: str) -> list:
        """Get all active assignments for a spool.

        Legacy data may contain duplicate active rows for the same spool_id,
        so callers should tolerate multiple results.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM filament_assignments "
            "WHERE spool_id = ? ORDER BY printer_id, tool_index",
            (spool_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_assignments(self) -> dict:
        """Return {printer_id: spool_id} for tool 0 (backward compat).

        Also includes a '_multi' key with full per-tool data:
        {printer_id: [{tool_index, spool_id}, ...]}
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT printer_id, tool_index, spool_id "
            "FROM filament_assignments ORDER BY printer_id, tool_index"
        ).fetchall()
        conn.close()
        # Backward-compatible flat dict (tool 0 only)
        flat = {}
        # Full multi-tool dict
        multi = {}  # type: dict
        for r in rows:
            pid = r["printer_id"]
            if r["tool_index"] == 0:
                flat[pid] = r["spool_id"]
            if pid not in multi:
                multi[pid] = []
            multi[pid].append({
                "tool_index": r["tool_index"],
                "spool_id": r["spool_id"],
            })
        flat["_multi"] = multi
        return flat
