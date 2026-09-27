"""The terminal view end to end, as a user without tmux or herdr would run it.

Two harnesses:
- a raw pseudo-terminal (what every terminal app gives a program): keys in, clean exit, terminal left sane
- tmux used only as a terminal emulator, to read back exactly what is on screen after each key, click and resize

In both, the view runs with a PATH that has no tmux or herdr, a fake ~/.claude and its own port.
"""
import os
import pty
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import fcntl
import time
import unittest

from tests import ROOT, FakeClaude, line, stamp, tool_result, tool_use

TUI = str(ROOT / 'server' / 'tui.py')
ANSI = re.compile(rb'\x1b\[[0-9;?<]*[ -/]*[@-~]|\x1b[()][0-9A-Za-z]|\x1b[=>]')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Fixture:
    """Fake home with three sessions, and an environment with no tmux or herdr on PATH."""

    def __enter__(self):
        self.procs = [subprocess.Popen(['sleep', '120']) for _ in range(3)]   # live pids for the fake sessions
        self.fake = FakeClaude().__enter__()
        asking, busy, idle = (p.pid for p in self.procs)
        self.fake.session('s-asking', [
            line(type='ai-title', aiTitle='Clean the build'),
            line(type='assistant', timestamp=stamp(20), message={'content': [tool_use('a1', 'Bash', command='rm -rf build', description='Delete build dir')]}),
        ], name='asking', status='waiting', waiting_for='permission prompt', pid=asking, updated=3000)
        self.fake.session('s-busy', [
            line(type='assistant', timestamp=stamp(30), message={'content': [tool_use('b1', 'Read', file_path='/w/app.py')]}),
            line(type='user', timestamp=stamp(29), message={'content': [tool_result('b1', 'print("hi")')]}),
            line(type='assistant', timestamp=stamp(10), message={'content': [tool_use('b2', 'Agent', description='review parser')]}),
        ], name='busy', status='busy', pid=busy, updated=2000, subagents=[
            ('r1', {'agentType': 'Explore', 'description': 'review parser', 'toolUseId': 'b2'},
             [line(type='assistant', timestamp=stamp(2), message={'content': [tool_use('r-1', 'Grep', pattern='def feed')]})]),
        ])
        self.fake.session('s-idle', [
            line(type='user', timestamp=stamp(60), message={'content': 'hello'}),
        ], name='idle', status='idle', pid=idle, updated=1000, subagents=[
            ('old', {'agentType': 'general', 'description': 'finished helper'},
             [line(type='assistant', timestamp=stamp(50), message={'content': [tool_use('o1', 'Read', file_path='/w/x')]}),
              line(type='user', timestamp=stamp(49), message={'content': [tool_result('o1', 'x')]})]),
        ])
        # PATH with only what the view needs: no tmux, no herdr, so jumping is off exactly as for most users
        self.bin = tempfile.TemporaryDirectory()
        for tool in ('ps', 'sh'):
            os.symlink(shutil.which(tool), os.path.join(self.bin.name, tool))
        self.env = {'HOME': str(self.fake.dir.parent), 'PATH': self.bin.name, 'TERM': 'xterm-256color',
                    'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8', 'HIVEMAP_PORT': str(free_port())}
        return self

    def __exit__(self, *exc):
        for p in self.procs:
            p.kill()
            p.wait()
        self.bin.cleanup()
        self.fake.__exit__(*exc)


class PlainTerminalTest(unittest.TestCase):
    """A bare pseudo-terminal: no multiplexer, no herdr, nothing but the tty."""

    @staticmethod
    def spawn(env, rows=24, cols=100):
        """The view on a fresh pseudo-terminal. subprocess, not pty.fork(): forking a threaded test process can deadlock."""
        fd, tty = pty.openpty()
        fcntl.ioctl(tty, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
        proc = subprocess.Popen([sys.executable, TUI], stdin=tty, stdout=tty, stderr=tty, env=env, start_new_session=True)
        return proc, fd, tty

    def run_tui(self, keys, rows=24, cols=100):
        with Fixture() as fx:
            proc, fd, tty = self.spawn(fx.env, rows, cols)
            before = termios.tcgetattr(tty)
            out = self.read(fd, 1.5)
            for k in keys:
                os.write(fd, k)
                out += self.read(fd, 0.4)
            os.write(fd, b'q')
            out += self.read(fd, 1.0)
            status = proc.wait(timeout=10)
            after = termios.tcgetattr(tty)
            os.close(fd), os.close(tty)
        return out, status, before, after

    @staticmethod
    def read(fd, seconds):
        out, end = b'', time.time() + seconds
        while time.time() < end:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    out += os.read(fd, 65536)
                except OSError:   # child exited and closed the tty
                    break
        return out

    def test_runs_and_exits_cleanly(self):
        # into a call and back out, page, then from the tree: hide done, fold, try to jump
        out, status, before, after = self.run_tui([b'j', b'j', b'\t', b'\r', b'\x1b[D', b'\x1b[6~', b'\x1b[D',
                                                   b'd', b' ', b'\r'])
        self.assertEqual(status, 0, out[-400:])
        text = ANSI.sub(b'', out).decode('utf-8', 'replace')
        for name in ('asking', 'busy', 'idle', 'waiting: permission prompt', 'no herdr or tmux pane'):
            self.assertIn(name, text)
        self.assertNotIn('Traceback', text)
        # entered the alternate screen and mouse mode, and left both, with the cursor back
        self.assertTrue(out.startswith(b'\x1b[?1049h'), out[:40])
        for restore in (b'\x1b[?1000l', b'\x1b[?1006l', b'\x1b[?25h', b'\x1b[?1049l'):
            self.assertIn(restore, out[-200:])
        # the terminal's own settings are back as they were: echo and line editing on
        self.assertEqual(after[3] & (termios.ECHO | termios.ICANON), before[3] & (termios.ECHO | termios.ICANON))

    def test_keys_arriving_together_act_in_order(self):
        # key repeat or paste: one read carries several keys; each must act on what the previous one left
        # from 'asking', move down to 'busy' and open its newest call, all in a single read
        out, status, _, _ = self.run_tui([b'jj\t\r'])
        text = ANSI.sub(b'', out).decode('utf-8', 'replace')
        self.assertIn('description: review parser', text, "opened busy's newest call (the Agent)")
        self.assertNotIn('command: rm -rf build', text, "did not open the call of the row the keys started on")

    def test_ctrl_c_also_restores_the_terminal(self):
        with Fixture() as fx:
            proc, fd, tty = self.spawn(fx.env)
            before = termios.tcgetattr(tty)
            out = self.read(fd, 1.5)
            proc.send_signal(signal.SIGINT)
            out += self.read(fd, 1.0)
            status = proc.wait(timeout=10)
            after = termios.tcgetattr(tty)
            os.close(fd), os.close(tty)
        self.assertEqual(status, 0, out[-300:])
        self.assertIn(b'\x1b[?1049l', out[-200:])
        self.assertEqual(after[3] & (termios.ECHO | termios.ICANON), before[3] & (termios.ECHO | termios.ICANON))


@unittest.skipUnless(shutil.which('tmux'), 'tmux not installed (used here only as a terminal emulator)')
class ScreenTest(unittest.TestCase):
    """What the user actually sees, read back from a terminal emulator after each input."""

    def setUp(self):
        self.fx = Fixture().__enter__()
        self.sock = f'hivemap-e2e-{os.getpid()}'
        env_args = [a for k, v in self.fx.env.items() for a in ('-e', f'{k}={v}')]
        self.tmux('new-session', '-d', '-s', 'v', '-x', '110', '-y', '24', *env_args,
                  f'{sys.executable} {TUI}; echo EXITED=$?; sleep 30')

    def tearDown(self):
        subprocess.run(['tmux', '-L', self.sock, 'kill-server'], capture_output=True)
        self.fx.__exit__(None, None, None)

    def tmux(self, *args):
        r = subprocess.run(['tmux', '-L', self.sock, *args], capture_output=True, text=True, timeout=5)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def screen(self):
        return self.tmux('capture-pane', '-p', '-t', 'v')

    def keys(self, *keys):
        self.tmux('send-keys', '-t', 'v', *keys)

    def raw(self, data):
        self.tmux('send-keys', '-t', 'v', '-l', data)

    def wait_for(self, predicate, what, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            s = self.screen()
            if predicate(s):
                return s
            time.sleep(0.1)
        self.fail(f'timed out waiting for {what}; screen was:\n{s}')

    def test_a_user_session(self):
        s = self.wait_for(lambda s: 'asking' in s and 'busy' in s, 'the session list')
        rows = [r for r in s.splitlines() if r.strip()]
        self.assertRegex(rows[0], r'^▾ 1 ! asking', 'the waiting session comes first, marked !')
        self.assertIn('waiting: permission prompt', rows[0], 'its header says why it waits')
        self.assertIn('Delete build dir', s, 'its pending call is listed')

        self.keys('Down', 'Down')   # asking › main, then busy
        self.wait_for(lambda s: 'busy  busy' in s, 'the busy session in the inspector')

        self.keys('Down', 'Down')   # busy › main › review parser (the running subagent)
        s = self.wait_for(lambda s: 'Explore · running' in s, 'the subagent in the inspector')
        self.assertIn('def feed', s)

        self.keys('Up', 'Up', 'Up', 'Up', 'Tab', 'Enter')   # back to asking, into its calls, open the call
        s = self.wait_for(lambda s: 'input' in s and 'command: rm -rf build' in s, 'the opened call')
        self.assertIn('still running…', s, 'a call awaiting permission has no output yet')

        self.keys('Left', 'Left')   # back out to the tree
        self.wait_for(lambda s: 'tab calls' in s, 'the tree footer')

        s = self.screen()
        self.assertIn('finished helper', s)
        self.keys('d')   # hide finished subagents
        s = self.wait_for(lambda s: 'finished helper' not in s, 'finished subagents hidden')
        self.assertIn('d show done', s)

        # a mouse click on the third row (the busy session), as the terminal would send it
        self.raw('\x1b[<0;10;3M')   # column 10, row 3
        self.wait_for(lambda s: 'busy  busy' in s, 'the clicked session in the inspector')

        self.keys('Enter')   # jump: without tmux/herdr on PATH it says so instead of failing
        self.wait_for(lambda s: 'no herdr or tmux pane for busy' in s, 'the no-jump message')

        self.tmux('resize-window', '-t', 'v', '-x', '60', '-y', '12')   # a small window still draws
        s = self.wait_for(lambda s: 'asking' in s and len(s.splitlines()[0]) <= 60, 'the resized screen')

        self.keys('q')
        s = self.wait_for(lambda s: 'EXITED=' in s, 'the view to quit')
        self.assertIn('EXITED=0', s)
        self.assertNotIn('Traceback', s)


if __name__ == '__main__':
    unittest.main()
