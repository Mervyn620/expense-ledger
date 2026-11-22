"""Exercise release selection in disposable workspaces without starting an app."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.name == "nt", "Windows desktop launcher")
class DesktopLaunchTests(unittest.TestCase):
    def workspace(self, root):
        (root / "desktop").mkdir()
        (root / "tools").mkdir()
        (root / "desktop/Expenses.Desktop.csproj").write_text(
            "<Project><PropertyGroup><Version>9.8.7</Version></PropertyGroup></Project>")
        shutil.copy2(PROJECT / "Start-Expenses.ps1", root)
        shutil.copy2(PROJECT / "tools/build.ps1", root / "tools")
        wrapper = root / "probe.ps1"
        wrapper.write_text("""
$ErrorActionPreference = 'Stop'
function Start-Process {
    param($FilePath, $WorkingDirectory, $WindowStyle)
    @{exe=$FilePath; root=$WorkingDirectory; window_style=$WindowStyle} | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $PSScriptRoot 'launched.json') -Encoding UTF8
}
& (Join-Path $PSScriptRoot 'Start-Expenses.ps1')
""", encoding="utf-8-sig")
        return wrapper

    def run_script(self, script):
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-File", str(script)], capture_output=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW)

    def test_launcher_uses_project_version_after_directory_move(self):
        with tempfile.TemporaryDirectory(prefix="expense release ") as temp:
            root = Path(temp)
            wrapper = self.workspace(root)
            exe = root / "releases/9.8.7/Expenses.Desktop.exe"
            exe.parent.mkdir(parents=True)
            exe.touch()
            result = self.run_script(wrapper)
            self.assertEqual(result.returncode, 0, result.stderr)
            launch = json.loads((root / "launched.json").read_text(encoding="utf-8-sig"))
            self.assertTrue(Path(launch["exe"]).samefile(exe))
            self.assertTrue(Path(launch["root"]).samefile(root))
            self.assertEqual(launch["window_style"], "Normal")

    def test_missing_current_release_does_not_fall_back_to_old_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wrapper = self.workspace(root)
            old = root / "releases/0.13.3/Expenses.Desktop.exe"
            old.parent.mkdir(parents=True)
            old.touch()
            self.assertNotEqual(self.run_script(wrapper).returncode, 0)
            self.assertFalse((root / "launched.json").exists())
            self.assertTrue(old.exists())

    def test_build_preserves_existing_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.workspace(root)
            exe = root / "releases/9.8.7/Expenses.Desktop.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"existing release must survive")
            result = self.run_script(root / "tools/build.ps1")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"already exists", result.stderr)
            self.assertEqual(exe.read_bytes(), b"existing release must survive")
