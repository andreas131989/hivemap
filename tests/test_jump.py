"""Jumping to a session's terminal pane with tmux, against a real tmux server on a private socket."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from tests import overlay


@unittest.skipUnless(shutil.which('tmux'), 'tmux not installed')
class TmuxJumpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # a private tmux server (own socket dir) so the tester's own tmux is never touched
        env = {k: v for k, v in os.environ.items() if k != 'TMUX'}
        env['TMUX_TMPDIR'] = self.tmp.name
        self.env_patch = mock.patch.dict(os.environ, env, clear=True)
        self.env_patch.start()
        self.saved_tmux, overlay.TMUX = overlay.TMUX, shutil.which('tmux')
        # first window: a shell whose child stands in for Claude Code; a second window is then made active
        info = self.tmux('new-session', '-d', '-P', '-F', '#{pane_id} #{pane_pid} #{window_id}', '-s', 'hm', '-x', '80', '-y', '20',
                         'sh -c "sleep 60; :"')
        self.target_pane, shell, self.target_window = info.split()
        shell = int(shell)
        self.tmux('new-window', '-t', 'hm', 'sh -c "sleep 60; :"')
        for _ in range(50):   # the shell's child: our stand-in "claude" process
            kids = subprocess.run(['pgrep', '-P', str(shell)], capture_output=True, text=True).stdout.split()
            if kids:
                self.claude_pid = int(kids[0])
                break
            time.sleep(0.05)

    def tearDown(self):
        subprocess.run([overlay.TMUX, 'kill-server'], capture_output=True)
        self.env_patch.stop()
        overlay.TMUX = self.saved_tmux
        self.tmp.cleanup()

    def tmux(self, *args):
        r = subprocess.run([overlay.TMUX, *args], capture_output=True, text=True, timeout=5)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def active_window(self):
        return self.tmux('display-message', '-p', '-t', 'hm', '#{window_id}')

    def test_jump_selects_the_window_and_pane_running_the_session(self):
        self.assertNotEqual(self.active_window(), self.target_window)
        self.assertEqual(overlay.tmux_focus(self.claude_pid), self.target_pane)
        self.assertEqual(self.active_window(), self.target_window)

    def test_process_outside_tmux_is_not_found(self):
        self.assertIsNone(overlay.tmux_focus(os.getpid()))

    def test_focus_falls_back_to_tmux_without_herdr(self):
        saved, overlay.HERDR = overlay.HERDR, None
        try:
            self.assertEqual(overlay.focus(self.claude_pid), self.target_pane)
        finally:
            overlay.HERDR = saved


if __name__ == '__main__':
    unittest.main()
