"""
Filament Inventory Repository
==============================
SQLite-backed persistence for filament spool inventory.
Extracted from database.py — behavior preserved exactly.
"""
import sqlite3
from datetime import datetime, timezone
from typing import Optional


class FilamentInventoryDB:
    """Read/write access to the FilamentInventory.db."""

    MATERIALS = {
        "PLA": "PLA", "ABS": "ABS", "ASA": "ASA", "PETG": "PEG",
        "Nylon": "NYL", "Nylon6 + CF": "N6C", "Nylon6 + GF30": "N6G",
        "Nylon12 + CF": "N2C", "Nylon12 + GF30": "N2G",
        "PEEK": "PEK", "PEEK + CF10": "PKC", "PEKK-A": "PKK",
        "PPSU": "PSU", "FR PC-ABS": "FPA",
        "ULTEM 1010": "U10", "ULTEM 9085": "U85", "TPU": "TPU", "SEBS": "SEB",
    }
    DEPRECATED_CREATION_MATERIALS = frozenset()
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
