"""bin/hivemap, run for real with its own HOME and port so it never touches the user's setup."""
import os
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request

from tests import ROOT

LAUNCHER = str(ROOT / 'bin' / 'hivemap')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.port = free_port()
        self.env = {**os.environ, 'HOME': self.home.name, 'HIVEMAP_PORT': str(self.port),
                    'XDG_STATE_HOME': os.path.join(self.home.name, 'state'), 'HIVEMAP_NOTIFY': '0'}

    def tearDown(self):
        self.run_launcher('stop')
        self.home.cleanup()

    def run_launcher(self, *args):
        return subprocess.run([LAUNCHER, *args], env=self.env, capture_output=True, text=True, timeout=30)

    def up(self):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/api/select', timeout=2) as r:
                return r.status == 200
        except OSError:
            return False

    def test_syntax(self):
        self.assertEqual(subprocess.run(['bash', '-n', LAUNCHER]).returncode, 0)

    def test_start_status_stop(self):
        self.assertIn('not running', self.run_launcher('status').stdout)
        r = self.run_launcher('start')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.up())
        self.assertIn('is running', self.run_launcher('status').stdout)
        self.assertIn('stopped', self.run_launcher('stop').stdout)
        for _ in range(20):
            if not self.up():
                break
            time.sleep(0.1)
        self.assertFalse(self.up())
        self.assertIn('was not running', self.run_launcher('stop').stdout)

    def test_stop_leaves_other_processes_alone(self):
        # an unrelated process whose command line mentions the server file (an editor, a grep...)
        decoy = subprocess.Popen(['sh', '-c', 'sleep 30; : hivemap/server/overlay.py'])
        try:
            self.run_launcher('start')
            self.run_launcher('stop')
            time.sleep(0.2)
            self.assertIsNone(decoy.poll(), 'stop must only kill the hivemap server')
        finally:
            decoy.kill()
            decoy.wait()

    def test_stop_only_stops_its_own_port(self):
        other = {**self.env, 'HIVEMAP_PORT': str(free_port())}
        subprocess.run([LAUNCHER, 'start'], env=other, capture_output=True, timeout=30)
        try:
            self.run_launcher('start')
            self.run_launcher('stop')
            self.assertIn('is running', subprocess.run([LAUNCHER, 'status'], env=other, capture_output=True, text=True).stdout)
        finally:
            subprocess.run([LAUNCHER, 'stop'], env=other, capture_output=True, timeout=30)

    def test_unknown_command(self):
        r = self.run_launcher('launch')
        self.assertEqual(r.returncode, 2)
        self.assertIn('usage:', r.stderr)

    def test_pane_outside_herdr(self):
        env = {k: v for k, v in self.env.items() if not k.startswith('HERDR')}
        r = subprocess.run([LAUNCHER, 'pane'], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn('not inside herdr', r.stderr)


if __name__ == '__main__':
    unittest.main()
