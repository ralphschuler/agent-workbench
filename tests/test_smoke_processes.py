"""Exercise cleanup with real descendants rather than process mocks."""

import importlib.util
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest


SPEC = importlib.util.spec_from_file_location(
    'process_smoke', Path(__file__).resolve().parents[1] / 'scripts/smoke.py')
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'pidfd_open'),
                     'The desktop smoke runs on Linux with pidfd support')
class ProcessCleanupTests(unittest.TestCase):
    def check_cleanup(self, *, exit_leader, ignore_term):
        with tempfile.TemporaryDirectory() as temporary:
            child_code = textwrap.dedent(f'''
                import os, signal, time
                from pathlib import Path
                if {ignore_term!r}:
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                directory = Path({temporary!r}) / 'profile' / 'Default'
                directory.mkdir(parents=True)
                print(os.getpid(), flush=True)
                while True:
                    (directory / 'ongoing-write').write_text('profile update')
                    time.sleep(0.01)
            ''')
            leader_code = textwrap.dedent(f'''
                import subprocess, sys, time
                subprocess.Popen([sys.executable, '-c', {child_code!r}])
                if not {exit_leader!r}:
                    time.sleep(60)
            ''')
            leader = subprocess.Popen(
                [sys.executable, '-c', leader_code], start_new_session=True,
                stdout=subprocess.PIPE, text=True,
            )
            descriptor = None
            try:
                readable, _, _ = select.select([leader.stdout], [], [], 5)
                self.assertTrue(readable, 'Child did not become ready')
                child_pid = int(leader.stdout.readline())
                descriptor = os.pidfd_open(child_pid)
                if exit_leader:
                    leader.wait(timeout=5)
                    self.assertEqual(leader.returncode, 0)
                SMOKE.stop_process_group(leader, timeout=0.2)
                self.assertIsNotNone(leader.returncode)
                ready, _, _ = select.select([descriptor], [], [], 0)
                self.assertTrue(ready, 'Owned child is still alive after group cleanup')
            finally:
                try:
                    os.killpg(leader.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                leader.wait(timeout=5)
                leader.stdout.close()
                if descriptor is not None:
                    select.select([descriptor], [], [], 5)
                    os.close(descriptor)
            # TemporaryDirectory must now remove the actual profile without a
            # background writer racing rmdir, as happened in the browser smoke.

    def test_stops_running_leader_and_child_before_profile_cleanup(self):
        self.check_cleanup(exit_leader=False, ignore_term=False)

    def test_kills_term_resistant_child_even_after_leader_has_exited(self):
        self.check_cleanup(exit_leader=True, ignore_term=True)
