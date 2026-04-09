"""
Inventory Service
==================
Business logic for filament spool inventory operations.
Validates input and delegates persistence to the repository.
"""


class InventoryService:
    """Encapsulates inventory business rules extracted from route handlers."""

    def __init__(self, repository):
        self.repository = repository

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_inventory(self, material=None, brand=None, color=None,
                      supplier=None):
        """Return filtered inventory list."""
        return self.repository.get_all(material, brand, color, supplier)

    def get_spool(self, spool_id):
        """Return a single spool or None."""
        return self.repository.get_by_id(spool_id)

    def get_options(self):
        """Return dropdown options for the inventory UI."""
        return {
            "materials": self.repository.get_materials_list(),
            "filter_materials": self.repository.get_filter_materials_list(),
            "form_materials": self.repository.get_creation_materials_list(),
            "brands": self.repository.get_brands_list(),
            "suppliers": self.repository.get_suppliers_list(),
        }

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def add_spool(self, data):
        """Validate and create a new filament spool.

        Returns dict with 'success', 'id', 'spool_id' on success.
        Raises ValueError on validation failure.
        """
        if not data:
            raise ValueError("No data provided")

        required = ["material", "brand", "color", "supplier",
                     "grams", "diameter", "operator"]
        missing = [f for f in required if not data.get(f)]
        if missing:
            raise ValueError(f"Missing fields: {', '.join(missing)}")

        try:
            grams = int(data["grams"])
            diameter = float(data["diameter"])
        except (TypeError, ValueError):
            raise ValueError("Invalid numeric values for grams/diameter")

        if grams <= 0:
            raise ValueError("grams must be > 0")
        if diameter <= 0:
            raise ValueError("diameter must be > 0")

        material = self._validate_material(data["material"])

        supplier = str(data["supplier"]).strip()
        if supplier not in self.repository.ALLOWED_SUPPLIERS:
            allowed = ", ".join(self.repository.ALLOWED_SUPPLIERS)
            raise ValueError(
                f"Invalid supplier '{supplier}'. Allowed suppliers: {allowed}"
            )

        spool_id = self.repository.add_filament(
            material=material,
            brand=data["brand"],
            color=data["color"],
            supplier=supplier,
            grams=grams,
            diameter=diameter,
            batch=data.get("batch", ""),
            operator=data["operator"],
        )
        return {"success": True, "id": spool_id, "spool_id": spool_id}

    def update_weight(self, spool_id, data):
        """Validate and update spool weight.

        Returns True on success.
        Raises ValueError on validation failure, KeyError if spool not found.
        """
        if not data or "grams" not in data:
            raise ValueError("Missing 'grams' field")

        try:
            grams = int(data["grams"])
        except (TypeError, ValueError):
            raise ValueError("Invalid grams value")

        if grams < 0:
            raise ValueError("grams must be >= 0")

        success = self.repository.update_weight(spool_id, grams)
        if not success:
            raise KeyError("Spool not found")
        return True

    def delete_spool(self, spool_id):
        """Delete a spool. Returns True on success.

        Raises KeyError if spool not found.
        """
        success = self.repository.delete_spool(spool_id)
        if not success:
            raise KeyError("Spool not found")
        return True

    # ------------------------------------------------------------------
    # Internal validation
    # ------------------------------------------------------------------

    def _validate_material(self, material):
        """Validate and normalize a material value."""
        material = str(material or "").strip()
        if not material:
            raise ValueError("material is required")
        if material in self.repository.DEPRECATED_CREATION_MATERIALS:
            raise ValueError(
                f"Material '{material}' is deprecated and cannot be used "
                f"for new or updated filament entries"
            )
        return material
