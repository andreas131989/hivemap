"""The terminal view's pure pieces: input splitting, rows, layout helpers."""
import re
import time
import unittest

from tests import overlay  # noqa: F401  (puts server/ on the path)
import tui

ANSI = re.compile(r'\x1b\[[0-9;]*m')


def plain(s):
    return ANSI.sub('', s)


def agent(aid, parent, running, events=()):
    return {'id': aid, 'parent': parent, 'running': running, 'label': aid, 'type': 't', 'model': 'claude-x',
            'ctx': 0, 'events': list(events)}


def session(sid, agents, status='idle'):
    return {'id': sid, 'name': sid, 'status': status, 'ctx': 12000, 'model': 'claude-x', 'cwd': '/w', 'title': None,
            'prompt': None, 'pid': 1, 'agents': agents}


class KeysTest(unittest.TestCase):
    def test_plain_keys_and_escape_sequences(self):
        self.assertEqual(tui.split_keys('jk\x1b[A\x1b[5~\x1bOB q'), (['j', 'k', '\x1b[A', '\x1b[5~', '\x1bOB', ' ', 'q'], ''))

    def test_mouse_event_cut_by_a_read_is_kept_whole(self):
        keys, rest = tui.split_keys('j\x1b[<65;30;1')
        self.assertEqual((keys, rest), (['j'], '\x1b[<65;30;1'))
        self.assertEqual(tui.split_keys(rest + '0M'), (['\x1b[<65;30;10M'], ''))
        self.assertEqual(tui.split_keys('\x1b[A\x1bO'), (['\x1b[A'], '\x1bO'))

    def test_mouse_regex(self):
        self.assertEqual(tui.MOUSE.match('\x1b[<0;12;7M').groups(), ('0', '12', '7'))
        self.assertIsNone(tui.MOUSE.match('\x1b[<0;12;7m'), 'releases are ignored')


class RowsTest(unittest.TestCase):
    def setUp(self):
        self.st = {'sessions': [session('s', [agent('main', None, False), agent('x', 'main', True), agent('y', 'x', False)]),
                                session('t', [agent('main', None, False)])]}

    def test_tree_depths(self):
        self.assertEqual([(r[0], r[3]) for r in tui.rows(self.st, set())],
                         [(('s', None), 0), (('s', 'main'), 1), (('s', 'x'), 2), (('s', 'y'), 3), (('t', None), 0), (('t', 'main'), 1)])

    def test_folded_sessions_show_only_their_row(self):
        self.assertEqual([r[0] for r in tui.rows(self.st, {'s'})], [('s', None), ('t', None), ('t', 'main')])

    def test_hide_done_keeps_main_and_running(self):
        self.assertEqual([r[0] for r in tui.rows(self.st, set(), hide_done=True)],
                         [('s', None), ('s', 'main'), ('s', 'x'), ('t', None), ('t', 'main')])

    def test_waiting_session_is_marked(self):
        s = session('w', [agent('main', None, False)], status='waiting')
        self.assertIn(' ! ', plain(tui.row_line((('w', None), s, None, 0), 1, set(), 40, False)))


class LayoutTest(unittest.TestCase):
    def test_fit_cuts_and_pads_to_width(self):
        self.assertEqual(plain(tui.fit([('1', 'hello'), ('', ' world')], 8)), 'hello wo')
        self.assertEqual(len(plain(tui.fit([('', 'hi')], 6))), 6)

    def test_wrap_strips_terminal_escapes_from_tool_output(self):
        lines = tui.wrap('ok \x1b[31mred\x1b[0m \x1b]0;title\x07done\tx\x07', 80)
        self.assertEqual(lines, ['ok red done x'], 'escapes removed before tab stops are counted')
        self.assertEqual(tui.wrap('abcdef', 4), ['abcd', 'ef'])

    def test_js_num_matches_javascript(self):
        self.assertEqual(tui.js_num(1767261601.25), '1767261601.25')
        self.assertEqual(tui.js_num(1767261600.0), '1767261600')

    def test_call_list_keeps_the_selected_call_visible(self):
        now = time.time()
        events = [{'t0': now - i, 't1': now, 'n': f'c{i}', 's': '', 'c': 'read', 'err': False} for i in range(50)]
        head = [[('', 'h')]]
        lines, scroll, start = tui.calls(head, events, now, 60, 11, 0, cur=3)
        shown = [plain(x) for x in lines[1:]]
        self.assertTrue(any(' c3 ' in x for x in shown), shown)
        self.assertEqual(len(lines), 11)

    def test_call_detail_shows_input_and_output(self):
        now = time.time()
        e = {'t0': now - 2, 't1': now, 'n': 'Bash', 's': '', 'c': 'exec', 'err': False, 'in': 'command: ls', 'out': 'a\nb'}
        text = [plain(x).rstrip() for x in tui.call_detail(e, now, 40, 20, 0)[0]]
        self.assertIn('command: ls', text)
        self.assertEqual(text[text.index('output') + 1:text.index('output') + 3], ['a', 'b'])
        e.update(t1=None, out=None)
        self.assertIn('still running…', [plain(x).rstrip() for x in tui.call_detail(e, now, 40, 20, 0)[0]])
        self.assertIn('not run yet: waiting for your approval', [plain(x).rstrip() for x in tui.call_detail(e, now, 40, 20, 0, pending=True)[0]])


if __name__ == '__main__':
    unittest.main()
