"""Exercise the real launcher in a disposable workspace; never launch Chrome."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher")
class DesktopBrowserLauncherTests(unittest.TestCase):
    def test_shared_profile_initialization_and_interactive_window(self):
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        with tempfile.TemporaryDirectory(prefix="expense browser fixture ") as temp:
            base = Path(temp)
            workspace = base / "desktop backend"
            source = base / "original workspace"
            (workspace / "scripts").mkdir(parents=True)
            (workspace / "config").mkdir()
            (source / "config").mkdir(parents=True)
            (source / "config/workflow.json").write_text("{}")
            profile = source / ".codex-shop-browser/chrome-profile"
            for name in ("start-shop-browser.ps1", "desktop-browser.ps1"):
                shutil.copy2(scripts / name, workspace / "scripts" / name)
            config = {
                "contract": "expense-desktop-shared-browser/v1",
                "source_workspace": str(source),
                "profile_path": str(profile),
                "port": 19323,
            }
            (workspace / "config/desktop-browser.json").write_text(json.dumps(config))
            (base / "chrome.exe").touch()
            # Overrides exist only in this child process; no real browser is started.
            wrapper = base / "probe.ps1"
            wrapper.write_text(r"""
param([switch]$Interactive)
$ErrorActionPreference = 'Stop'
function Get-Command {
    param($Name, $ErrorAction)
    if ($Name -ne 'chrome.exe') { throw 'Unexpected command lookup' }
    [pscustomobject]@{Source=(Join-Path $PSScriptRoot 'chrome.exe')}
}
function Get-ItemProperty { param($Path, $ErrorAction) }
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle)
    [pscustomobject]@{
        FilePath=$FilePath; Arguments=$ArgumentList;
        WorkingDirectory=$WorkingDirectory; WindowStyle=$WindowStyle
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'launch.json') -Encoding UTF8
}
& (Join-Path $PSScriptRoot 'desktop backend\scripts\start-shop-browser.ps1') -StartUrl 'about:blank' -Interactive:$Interactive
""", encoding="utf-8-sig")
            state = workspace / ".codex-shop-browser"
            self.assertFalse(state.exists())
            for interactive, expected in ((False, "Hidden"), (True, "Normal")):
                with self.subTest(interactive=interactive):
                    command = ["powershell.exe", "-NoProfile", "-NonInteractive",
                               "-ExecutionPolicy", "Bypass", "-File", str(wrapper)]
                    if interactive:
                        command.append("-Interactive")
                    result = subprocess.run(command, capture_output=True, timeout=20,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                    self.assertEqual((state / "debug-port.txt").read_text().strip(), "19323")
                    launch = json.loads((base / "launch.json").read_text(encoding="utf-8-sig"))
                    self.assertEqual(launch["WindowStyle"], expected)
                    self.assertTrue(Path(launch["WorkingDirectory"]).samefile(workspace))
                    self.assertIn(f'"--user-data-dir={profile}"', launch["Arguments"])
                    self.assertIn("--remote-debugging-port=19323", launch["Arguments"])
                    self.assertIn("about:blank", launch["Arguments"])
                    self.assertTrue(profile.is_dir())
                    self.assertFalse((state / "chrome-profile").exists())


    def test_automatic_reconciliation_runs_only_after_confirmed_login(self):
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        for outcome in ("success", "failed", "closed"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                shutil.copy2(scripts / "login-and-reconcile-scotia.ps1", base)
                login = "param([switch]$WaitForLogin)\nif (-not $WaitForLogin) { throw 'Missing login gate' }\n"
                login += {
                    "success": "$global:LASTEXITCODE = 0\n",
                    "failed": "$global:LASTEXITCODE = 1\n",
                    "closed": "throw 'Login page closed'\n",
                }[outcome]
                (base / "prepare-scotia-login.ps1").write_text(login, encoding="utf-8-sig")
                (base / "weekly-expense-refresh.ps1").write_text(
                    "param([switch]$ReconcileScotia)\n"
                    "if (-not $ReconcileScotia) {throw 'Missing reconciliation flag'}\n"
                    "Set-Content -LiteralPath (Join-Path $PSScriptRoot 'collected.txt') -Value 'once'\n"
                    "$global:LASTEXITCODE = 0\n", encoding="utf-8-sig")
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-File", str(base / "login-and-reconcile-scotia.ps1")],
                    capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(result.returncode == 0, outcome == "success", result.stderr)
                self.assertEqual((base / "collected.txt").exists(), outcome == "success")


if __name__ == "__main__":
    unittest.main()
