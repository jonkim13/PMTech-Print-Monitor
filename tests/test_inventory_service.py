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


class UpdateSpoolServiceTests(unittest.TestCase):
    def test_update_spool_happy_path_updates_all_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, repo = _make_service(tmpdir)
            spool_id = _seed_spool(service)

            service.update_spool(spool_id, {
                "grams": 750,
                "brand": "3DXTech Brand",
                "color": "Carbon Black",
                "supplier": "3DXTech",
                "batch": "L99",
            })

            updated = repo.get_by_id(spool_id)
            self.assertEqual(updated["grams"], 750)
            self.assertEqual(updated["brand"], "3DXTech Brand")
            self.assertEqual(updated["color"], "Carbon Black")
            self.assertEqual(updated["supplier"], "3DXTech")
            self.assertEqual(updated["batch"], "L99")

    def test_update_spool_changing_only_weight_keeps_other_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, repo = _make_service(tmpdir)
            spool_id = _seed_spool(service)
            before = repo.get_by_id(spool_id)

            service.update_spool(spool_id, {
                "grams": 500,
                "brand": before["brand"],
                "color": before["color"],
                "supplier": before["supplier"],
                "batch": before["batch"],
            })

            after = repo.get_by_id(spool_id)
            self.assertEqual(after["grams"], 500)
            self.assertEqual(after["brand"], before["brand"])
            self.assertEqual(after["color"], before["color"])
            self.assertEqual(after["supplier"], before["supplier"])
            self.assertEqual(after["batch"], before["batch"])

    def test_update_spool_rejects_invalid_supplier(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _ = _make_service(tmpdir)
            spool_id = _seed_spool(service)

            with self.assertRaises(ValueError):
                service.update_spool(spool_id, {
                    "grams": 500,
                    "brand": "Brand X",
                    "color": "Red",
                    "supplier": "Bogus Vendor",
                    "batch": "",
                })

    def test_update_spool_rejects_negative_weight(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _ = _make_service(tmpdir)
            spool_id = _seed_spool(service)

            with self.assertRaises(ValueError):
                service.update_spool(spool_id, {
                    "grams": -5,
                    "brand": "Prusament",
                    "color": "Black",
                    "supplier": "Prusa Research",
                    "batch": "",
                })

    def test_update_spool_unknown_spool_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service, _ = _make_service(tmpdir)

            with self.assertRaises(KeyError):
                service.update_spool("DOES_NOT_EXIST", {
                    "grams": 500,
                    "brand": "Prusament",
                    "color": "Black",
                    "supplier": "Prusa Research",
                    "batch": "",
                })

    def test_update_spool_repo_layer_rejects_invalid_supplier(self):
        """Belt-and-braces: bypass the service layer and call the repo
        directly. The supplier whitelist must still trip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            service, repo = _make_service(tmpdir)
            spool_id = _seed_spool(service)

            with self.assertRaises(ValueError):
                repo.update_spool(
                    spool_id,
                    grams=500,
                    brand="Brand X",
                    color="Red",
                    supplier="Bogus Vendor",
                    batch="",
                )


if __name__ == "__main__":
    unittest.main()
