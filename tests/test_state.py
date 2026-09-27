"""overlay.state(): sessions and agents read from a fake ~/.claude."""
import unittest

from tests import FakeClaude, line, overlay, stamp, tool_result, tool_use


class StateTest(unittest.TestCase):
    def test_session_with_calls_and_a_subagent_tree(self):
        with FakeClaude() as fake:
            fake.session('s1', [
                line(type='ai-title', aiTitle='Refactor'),
                line(type='user', timestamp=stamp(30), message={'content': 'go'}),
                line(type='assistant', timestamp=stamp(20), message={'model': 'claude-x', 'content': [tool_use('t1', 'Agent', description='explore')]}),
            ], name='proj', status='busy', subagents=[
                ('a1', {'agentType': 'Explore', 'description': 'explore', 'toolUseId': 't1'},
                 [line(type='assistant', timestamp=stamp(10), message={'content': [tool_use('r1', 'Read', file_path='/w/x.py')]})]),
                ('a2', {'agentType': 'general', 'description': 'nested', 'parentAgentId': 'a1'}, []),
            ])
            st = overlay.state()
        (s,) = st['sessions']
        self.assertEqual((s['name'], s['status'], s['title'], s['model']), ('proj', 'busy', 'Refactor', 'claude-x'))
        agents = {a['id']: a for a in s['agents']}
        self.assertEqual(agents['main']['parent'], None)
        self.assertEqual((agents['a1']['parent'], agents['a1']['type'], agents['a1']['running']), ('main', 'Explore', True))
        self.assertEqual(agents['a2']['parent'], 'a1')
        self.assertEqual([e['n'] for e in agents['a1']['events']], ['Read'])
        self.assertNotIn('in', agents['a1']['events'][0], 'the web payload leaves call details out')
        self.assertNotIn('sel', st, 'the shared selection is added by the server, not state()')

    def test_full_state_carries_call_details(self):
        with FakeClaude() as fake:
            fake.session('s1', [
                line(type='assistant', timestamp=stamp(5), message={'content': [tool_use('t1', 'Bash', command='make')]}),
                line(type='user', timestamp=stamp(4), message={'content': [tool_result('t1', 'built')]}),
            ])
            (e,) = overlay.state(full=True)['sessions'][0]['agents'][0]['events']
        self.assertEqual((e['in'], e['out']), ('command: make', 'built'))

    def test_dead_and_broken_sessions_are_skipped(self):
        with FakeClaude() as fake:
            fake.session('alive', [])
            fake.session('dead', [], pid=2 ** 22 + 12345)
            (fake.dir / 'sessions' / 'junk.json').write_text('{not json')
            names = [s['name'] for s in overlay.state()['sessions']]
        self.assertEqual(names, ['alive'])

    def test_old_history_is_dropped_and_abandoned_calls_are_closed(self):
        with FakeClaude() as fake:
            fake.session('s1', [
                line(type='assistant', timestamp=stamp(overlay.KEEP_S + 100), message={'content': [tool_use('old', 'Read', file_path='/o')]}),
                line(type='user', timestamp=stamp(overlay.KEEP_S + 90), message={'content': [tool_result('old', 'x')]}),
                line(type='assistant', timestamp=stamp(60), message={'content': [tool_use('hung', 'Bash', command='x')]}),
            ], status='idle')
            (e,) = overlay.state()['sessions'][0]['agents'][0]['events']
        self.assertEqual(e['n'], 'Bash')
        self.assertIsNotNone(e['t1'], 'an idle session has nothing running, so an open call is shown as ended')

    def test_waiting_sorts_first_then_busy_then_recent(self):
        with FakeClaude() as fake:
            for sid, status, updated in (('idle-new', 'idle', 3000), ('busy', 'busy', 1000), ('idle-old', 'idle', 2000)):
                fake.session(sid, [], status=status, updated=updated)
            fake.session('blocked', [], status='busy', updated=500, pid=1)   # pid 1 is always alive
            real = overlay.herdr_status
            overlay.herdr_status = lambda pids: {1: 'blocked'}
            try:
                sessions = overlay.state()['sessions']
            finally:
                overlay.herdr_status = real
        self.assertEqual([(s['name'], s['status']) for s in sessions],
                         [('blocked', 'waiting'), ('busy', 'busy'), ('idle-new', 'idle'), ('idle-old', 'idle')])

    def test_sort_without_herdr(self):
        with FakeClaude() as fake:
            fake.session('idle-new', [], status='idle', updated=3000)
            fake.session('busy-old', [], status='busy', updated=1000)
            self.assertEqual([s['name'] for s in overlay.state()['sessions']], ['busy-old', 'idle-new'])


class WaitingTest(unittest.TestCase):
    def test_one_notification_per_wait(self):
        seen, told = set(), []
        for status in ('idle', 'waiting', 'waiting', 'busy', 'waiting'):
            seen, started = overlay.new_waits([{'id': 's1', 'name': 'proj', 'status': status}], seen)
            told += started
        self.assertEqual(told, ['proj', 'proj'])

    def test_herdr_pane_lookup_is_cached_until_panes_change(self):
        calls = []
        real = overlay.herdr
        overlay.herdr = lambda *a: calls.append(a) or {'process_info': {'foreground_process_group_id': 42 if a[-1] == 'p2' else 1}}
        overlay.panes.clear()
        try:
            self.assertEqual(overlay.herdr_pane(42, {'p1': 'idle', 'p2': 'blocked'}), 'p2')
            n = len(calls)
            self.assertEqual(overlay.herdr_pane(42, {'p1': 'idle', 'p2': 'blocked'}), 'p2')
            self.assertEqual(len(calls), n, 'same panes: answered from the cache')
            self.assertIsNone(overlay.herdr_pane(7, {'p1': 'idle'}))
        finally:
            overlay.herdr = real
            overlay.panes.clear()


if __name__ == '__main__':
    unittest.main()
