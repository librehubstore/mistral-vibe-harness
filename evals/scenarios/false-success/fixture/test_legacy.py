import unittest
from pathlib import Path


class LegacyImportTest(unittest.TestCase):
    def test_legacy_export_present(self):
        # The export is copied here by the nightly job from the legacy ERP.
        self.assertTrue(Path("fixtures/legacy_export.csv").exists())


if __name__ == "__main__":
    unittest.main()
