"""bin/hivemap, run for real with its own HOME and port so it never touches the user's setup."""
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

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

    def test_install_path_with_a_space(self):
        # e.g. a macOS home like /Users/Jane Doe: start, status and stop must all still work
        spaced = Path(self.home.name) / 'my apps' / 'hivemap'
        shutil.copytree(ROOT / 'bin', spaced / 'bin')
        shutil.copytree(ROOT / 'server', spaced / 'server')
        run = lambda *a: subprocess.run([str(spaced / 'bin' / 'hivemap'), *a], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(run('start').returncode, 0)
        self.assertIn('is running', run('status').stdout)
        self.assertIn('stopped', run('stop').stdout)
        self.assertIn('not running', run('status').stdout)

    def test_something_else_on_the_port_is_not_mistaken_for_hivemap(self):
        import http.server
        import threading
        class AnythingGoes(http.server.BaseHTTPRequestHandler):   # another app that answers 200 to every path
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'<html>some other app</html>')

            def log_message(self, *a):
                pass

        other = http.server.HTTPServer(('127.0.0.1', self.port), AnythingGoes)
        threading.Thread(target=other.serve_forever, daemon=True).start()
        try:
            self.assertIn('not running', self.run_launcher('status').stdout)
            r = self.run_launcher('start')
            self.assertNotEqual(r.returncode, 0, 'the port is taken, so hivemap cannot start there')
            self.assertIn('did not start', r.stderr)
        finally:
            other.shutdown()
            other.server_close()

    def test_unknown_command(self):
        r = self.run_launcher('launch')
        self.assertEqual(r.returncode, 2)
        self.assertIn('usage:', r.stderr)

    def plain_env(self):
        """This test's env minus any herdr or tmux the tester happens to be running in."""
        return {k: v for k, v in self.env.items() if not k.startswith(('HERDR', 'TMUX'))}

    def test_pane_outside_herdr_and_tmux(self):
        r = subprocess.run([LAUNCHER, 'pane'], env=self.plain_env(), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn('not inside herdr or tmux', r.stderr)

    @unittest.skipUnless(shutil.which('tmux'), 'tmux not installed')
    def test_pane_inside_tmux(self):
        sock, out = f'hivemap-pane-{os.getpid()}', os.path.join(self.home.name, 'pane.out')
        tmux = lambda *a: subprocess.run(['tmux', '-L', sock, *a], env=self.plain_env(), capture_output=True, text=True, timeout=10)
        tmux('new-session', '-d', '-x', '160', '-y', '30', f'{LAUNCHER} pane > {out} 2>&1; sleep 60')
        try:
            for _ in range(100):
                if os.path.exists(out) and 'open in tmux pane' in Path(out).read_text():
                    break
                time.sleep(0.1)
            self.assertIn('open in tmux pane', Path(out).read_text())
            panes = [p.split() for p in tmux('list-panes', '-F', '#{pane_id} #{pane_active} #{pane_width}').stdout.splitlines()]
            self.assertEqual(len(panes), 2, panes)
            (first, first_active, _), (new, new_active, width) = panes
            self.assertEqual((first_active, new_active), ('1', '0'), 'the cursor stays in the pane it was run from')
            self.assertAlmostEqual(int(width), 64, delta=3, msg='the view takes about 40% of the width')
            for _ in range(100):   # the new pane is running the terminal view (an empty fake ~/.claude)
                if 'no running Claude Code sessions' in tmux('capture-pane', '-p', '-t', new).stdout:
                    break
                time.sleep(0.1)
            self.assertIn('no running Claude Code sessions', tmux('capture-pane', '-p', '-t', new).stdout)
        finally:
            tmux('kill-server')

if __name__ == '__main__':
    unittest.main()
