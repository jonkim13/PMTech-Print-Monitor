import os
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.inventory.repository import FilamentInventoryDB
from app.domains.inventory.service import InventoryService


def _make_service(tmpdir):
    repo = FilamentInventoryDB(os.path.join(tmpdir, "filament.db"))
    return InventoryService(repo), repo


def _seed_spool(service, supplier="Prusa Research", brand="Prusament",
                color="Galaxy Black"):
    return service.add_spool({
        "material": "PLA",
        "brand": brand,
        "color": color,
        "supplier": supplier,
        "grams": 1000,
        "diameter": 1.75,
        "batch": "L42",
        "operator": "phil",
    })["spool_id"]


class UpdateDetailsServiceTests(unittest.TestCase):
    def test_update_details_happy_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, repo = _make_service(tmpdir)
            spool_id = _seed_spool(service)

            service.update_details(spool_id, {
                "brand": "3DXTech Brand",
                "color": "Carbon Black",
                "supplier": "3DXTech",
                "batch": "L99",
            })

            updated = repo.get_by_id(spool_id)
            self.assertEqual(updated["brand"], "3DXTech Brand")
            self.assertEqual(updated["color"], "Carbon Black")
            self.assertEqual(updated["supplier"], "3DXTech")
            self.assertEqual(updated["batch"], "L99")

    def test_update_details_rejects_invalid_supplier(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _ = _make_service(tmpdir)
            spool_id = _seed_spool(service)

            with self.assertRaises(ValueError):
                service.update_details(spool_id, {
                    "brand": "Brand X",
                    "color": "Red",
                    "supplier": "Bogus Vendor",
                    "batch": "",
                })

    def test_update_details_unknown_spool_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _ = _make_service(tmpdir)

            with self.assertRaises(KeyError):
                service.update_details("DOES_NOT_EXIST", {
                    "brand": "Prusament",
                    "color": "Black",
                    "supplier": "Prusa Research",
                    "batch": "",
                })

    def test_update_details_repo_layer_rejects_invalid_supplier(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, repo = _make_service(tmpdir)
            spool_id = _seed_spool(service)

            with self.assertRaises(ValueError):
                repo.update_details(
                    spool_id,
                    brand="Brand X",
                    color="Red",
                    supplier="Bogus Vendor",
                    batch="",
                )


if __name__ == "__main__":
    unittest.main()
