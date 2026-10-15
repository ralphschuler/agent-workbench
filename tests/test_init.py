import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class InitializationTests(unittest.TestCase):
    def test_initialization_creates_private_credentials_and_preserves_existing_files(self):
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / 'scripts').mkdir()
            shutil.copy(repository / 'scripts/init.sh', target / 'scripts/init.sh')
            shutil.copy(repository / '.env.example', target / '.env.example')
            def initialize():
                return subprocess.run(['bash', str(target / 'scripts/init.sh')], check=True, capture_output=True, text=True)
            result = initialize()
            password = (target / '.secrets/vnc_password').read_text()
            config = (target / '.env').read_text()
            token = next(line.split('=', 1)[1] for line in config.splitlines() if line.startswith('HINDSIGHT_API_KEY='))
            self.assertEqual(len(password), 8)
            self.assertGreaterEqual(len(token), 32)
            self.assertNotIn(password, result.stdout + result.stderr)
            self.assertNotIn(token, result.stdout + result.stderr)
            if os.name == 'posix':
                self.assertEqual((target / '.secrets').stat().st_mode & 0o777, 0o700)
                self.assertEqual((target / '.secrets/vnc_password').stat().st_mode & 0o777, 0o444)
                self.assertEqual((target / 'workspace').stat().st_mode & 0o777, 0o755)
                self.assertEqual((target / '.env').stat().st_mode & 0o777, 0o600)
            (target / '.env').write_text(config + '\n# preserved user setting\n')
            initialize()
            self.assertEqual((target / '.secrets/vnc_password').read_text(), password)
            self.assertEqual((target / '.env').read_text(), config + '\n# preserved user setting\n')
