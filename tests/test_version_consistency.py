from pathlib import Path
import re
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class VersionConsistencyTests(unittest.TestCase):
    def test_package_and_installer_versions_match_project_version(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project_version = project["project"]["version"]

        package_source = (ROOT / "src" / "ped_hunter" / "__init__.py").read_text(encoding="utf-8")
        package_match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', package_source, re.MULTILINE)
        self.assertIsNotNone(package_match, "ped_hunter.__version__ is missing")
        assert package_match is not None

        installer_source = (ROOT / "installer" / "PED-Hunter.iss").read_text(encoding="utf-8")
        installer_match = re.search(r'^#define MyAppVersion\s+"([^"]+)"', installer_source, re.MULTILINE)
        self.assertIsNotNone(installer_match, "installer MyAppVersion is missing")
        assert installer_match is not None

        self.assertEqual(project_version, package_match.group(1))
        self.assertEqual(project_version, installer_match.group(1))


if __name__ == "__main__":
    unittest.main()
