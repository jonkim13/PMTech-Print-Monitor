"""Assignment-service / repository shape tests.

Pinning the explicit `{"primary": ..., "by_printer": ...}` shape so
the old `_multi` magic-key regression can't return.
"""

import os
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.assignments.repository import FilamentAssignmentDB


def _seed(tmpdir):
    repo = FilamentAssignmentDB(os.path.join(tmpdir, "assignments.db"))
    repo.assign("core_one_1", "SP001", tool_index=0)
    repo.assign("xl_1", "SP010", tool_index=0)
    repo.assign("xl_1", "SP011", tool_index=1)
    repo.assign("xl_1", "SP012", tool_index=2)
    return repo


class GetAllAssignmentsShapeTests(unittest.TestCase):
    def test_get_all_assignments_returns_primary_and_by_printer_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = _seed(tmpdir)

            result = repo.get_all_assignments()

            self.assertIn("primary", result)
            self.assertIn("by_printer", result)
            self.assertEqual(set(result.keys()), {"primary", "by_printer"})
            self.assertEqual(result["primary"]["core_one_1"], "SP001")
            self.assertEqual(result["primary"]["xl_1"], "SP010")

    def test_get_all_assignments_no_underscore_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = _seed(tmpdir)

            result = repo.get_all_assignments()

            for k in result.keys():
                self.assertFalse(
                    str(k).startswith("_"),
                    f"underscore-prefixed key {k!r} leaked into response",
                )

    def test_by_printer_contains_all_tools(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = _seed(tmpdir)

            result = repo.get_all_assignments()

            xl_tools = result["by_printer"]["xl_1"]
            self.assertEqual(len(xl_tools), 3)
            self.assertEqual(
                sorted((t["tool_index"], t["spool_id"]) for t in xl_tools),
                [(0, "SP010"), (1, "SP011"), (2, "SP012")],
            )
            self.assertEqual(
                result["by_printer"]["core_one_1"],
                [{"tool_index": 0, "spool_id": "SP001"}],
            )


if __name__ == "__main__":
    unittest.main()
