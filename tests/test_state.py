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

    def test_waiting_comes_from_claude_code_and_sorts_first(self):
        with FakeClaude() as fake:
            for sid, status, updated in (('idle-new', 'idle', 3000), ('busy', 'busy', 1000), ('idle-old', 'idle', 2000)):
                fake.session(sid, [], status=status, updated=updated)
            fake.session('asking', [
                line(type='assistant', timestamp=stamp(5), message={'content': [tool_use('t1', 'Bash', command='rm -rf build')]}),
            ], status='waiting', updated=500, waiting_for='permission prompt')
            sessions = overlay.state()['sessions']
        self.assertEqual([(s['name'], s['status'], s['waiting_for']) for s in sessions],
                         [('asking', 'waiting', 'permission prompt'), ('busy', 'busy', None),
                          ('idle-new', 'idle', None), ('idle-old', 'idle', None)])
        call = sessions[0]['agents'][0]['events'][0]
        self.assertIsNone(call['t1'], 'the call waiting for permission is still pending, not ended')

    def test_ended_sessions_are_forgotten(self):
        with FakeClaude() as fake:
            fake.session('s1', [line(type='user', timestamp=stamp(5), message={'content': 'hi'})], subagents=[
                ('a1', {'agentType': 'Explore', 'description': 'x'}, [line(type='user', timestamp=stamp(4), message={'content': 'go'})])])
            fake.session('s2', [])
            overlay.state()
            self.assertEqual(len(overlay.tails), 3, 'two transcripts and one subagent are tailed')
            (fake.dir / 'sessions' / 's1.json').unlink()   # s1 ends
            overlay.state()
            self.assertEqual(set(overlay.paths), {'s2'})
            self.assertEqual([p.name for p in overlay.tails], ['s2.jsonl'], 'its transcript and subagent tails are dropped')

    def test_busy_before_idle(self):
        with FakeClaude() as fake:
            fake.session('idle-new', [], status='idle', updated=3000)
            fake.session('busy-old', [], status='busy', updated=1000)
            self.assertEqual([s['name'] for s in overlay.state()['sessions']], ['busy-old', 'idle-new'])


class NotifyTest(unittest.TestCase):
    def run_statuses(self, steps):
        """steps: [(seconds, status, waiting_for)] for one session. Returns the messages sent."""
        seen, told = {}, []
        for t, status, why in steps:
            seen, messages = overlay.news([{'id': 's1', 'name': 'proj', 'status': status, 'waiting_for': why}], seen, t)
            told += messages
        return told

    def test_waiting_is_told_once_with_the_reason(self):
        self.assertEqual(self.run_statuses([(0, 'busy', None), (2, 'waiting', 'permission prompt'), (4, 'waiting', 'permission prompt'),
                                            (6, 'busy', None), (8, 'waiting', None)]),
                         ['proj is waiting on you (permission prompt)', 'proj is waiting on you'])

    def test_finished_only_after_real_work(self):
        self.assertEqual(self.run_statuses([(0, 'idle', None), (1, 'busy', None), (5, 'idle', None)]), [], 'a 4s turn is not news')
        self.assertEqual(self.run_statuses([(0, 'idle', None), (1, 'busy', None), (40, 'busy', None), (45, 'idle', None)]),
                         ['proj finished'])

    def test_first_sight_of_an_idle_session_is_not_news(self):
        self.assertEqual(self.run_statuses([(0, 'idle', None), (100, 'idle', None)]), [])


class JumpTest(unittest.TestCase):
    def test_herdr_pane_lookup_is_cached_until_panes_change(self):
        calls = []
        real = overlay.herdr
        overlay.herdr = lambda *a: calls.append(a) or {'process_info': {'foreground_process_group_id': 42 if a[-1] == 'p2' else 1}}
        overlay.panes.clear()
        try:
            self.assertEqual(overlay.herdr_pane(42, {'p1', 'p2'}), 'p2')
            n = len(calls)
            self.assertEqual(overlay.herdr_pane(42, {'p1', 'p2'}), 'p2')
            self.assertEqual(len(calls), n, 'same panes: answered from the cache')
            self.assertIsNone(overlay.herdr_pane(7, {'p1'}))
        finally:
            overlay.herdr = real
            overlay.panes.clear()


if __name__ == '__main__':
    unittest.main()
