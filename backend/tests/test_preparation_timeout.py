import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe')


@unittest.skipUnless(os.name == 'nt' and POWERSHELL, 'Windows preparation runner')
class PreparationTimeoutTest(unittest.TestCase):
    def run_preparation(self, source, timeout=5):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='.test-preparation-') as temp:
            Path(temp, 'check.py').write_text(source, encoding='utf-8')
            helper = str(ROOT / 'scripts/maintenance/bounded-process.ps1').replace("'", "''")
            python = sys.executable.replace("'", "''")
            command = (
                "$ErrorActionPreference='Stop'; "
                f". '{helper}'; "
                f"Invoke-BoundedProcess -FilePath '{python}' -Arguments @('check.py') "
                f"-Label 'Fixture preparation' -TimeoutSeconds {timeout}"
            )
            encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
            start = time.monotonic()
            result = subprocess.run(
                [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-EncodedCommand', encoded],
                cwd=temp, capture_output=True, timeout=20,
            )
            return result, time.monotonic() - start

    def test_preparation_drains_both_pipes_without_deadlocking(self):
        result, _ = self.run_preparation(
            "import sys\nsys.stderr.write('e' * 200000)\nsys.stdout.write('o' * 200000)\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'o' * 1000, result.stdout)

    def test_preparation_reports_nonzero_exit(self):
        result, _ = self.run_preparation("import sys\nsys.stderr.write('fixture failure')\nsys.exit(7)\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'exit 7', result.stderr)

    def test_stuck_preparation_is_terminated_with_specific_timeout(self):
        result, elapsed = self.run_preparation('import time\ntime.sleep(60)\n', timeout=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'preparation timeout', result.stderr)
        self.assertLess(elapsed, 15)


if __name__ == '__main__':
    unittest.main()
