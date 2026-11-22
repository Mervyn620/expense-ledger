import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VersionHistoryTest(unittest.TestCase):
    def test_application_versions_and_changelog_are_synchronized(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        settings = (ROOT / "app" / "settings.py").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"$', settings, re.MULTILINE)
        self.assertIsNotNone(match, "app/settings.py must declare APP_VERSION")

        version = package["version"]
        self.assertEqual(lock["version"], version)
        self.assertEqual(lock["packages"][""]["version"], version)
        self.assertEqual(match.group(1), version)
        self.assertRegex(
            changelog,
            rf"(?m)^## {re.escape(version)} - \d{{4}}-\d{{2}}-\d{{2}}$",
        )

    def test_readme_points_to_the_canonical_changelog(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("CHANGELOG.md", readme)


if __name__ == "__main__":
    unittest.main()
