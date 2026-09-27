"""The transcript parser: overlay.Tail."""
import tempfile
import unittest
from pathlib import Path

from tests import line, overlay, tool_result, tool_use


class TailTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 't.jsonl'

    def tearDown(self):
        self.tmp.cleanup()

    def test_prompt_call_and_result(self):
        self.path.write_text(
            line(type='user', timestamp='2026-01-01T10:00:00.000Z', message={'content': 'fix the bug'})
            + 'not json\n'
            + line(type='assistant', timestamp='2026-01-01T10:00:01.250Z', message={
                'model': 'claude-x', 'usage': {'input_tokens': 5, 'cache_read_input_tokens': 995},
                'content': [tool_use('tu1', 'Bash', command='ls\n-la', description='List files')]})
            + line(type='user', timestamp='2026-01-01T10:00:03.000Z', message={'content': [tool_result('tu1', 'boom', error=True)]}))
        t = overlay.Tail(self.path).poll()
        prompt, call = t.events
        self.assertEqual((prompt['n'], prompt['s'], prompt['in']), ('Prompt', 'fix the bug', 'fix the bug'))
        self.assertEqual((call['n'], call['s'], call['c']), ('Bash', 'List files', 'exec'))
        self.assertEqual(call['in'], 'command:\nls\n-la\ndescription: List files')
        self.assertEqual((call['out'], call['err'], call['t1'] - call['t0']), ('boom', True, 1.75))
        self.assertEqual((t.model, t.ctx), ('claude-x', 1000))
        self.assertFalse(t.open)

    def test_half_written_line_waits_for_the_rest(self):
        result = line(type='user', timestamp='2026-01-01T10:00:03Z', message={'content': [tool_result('tu1', 'ok')]})
        self.path.write_text(
            line(type='assistant', timestamp='2026-01-01T10:00:01Z', message={'content': [tool_use('tu1', 'Read', file_path='/a/b.py')]})
            + result[:30])
        t = overlay.Tail(self.path).poll()
        self.assertIn('tu1', t.open, 'no result yet: the call stays open')
        with open(self.path, 'a') as f:
            f.write(result[30:])
        t.poll()
        self.assertFalse(t.open)
        self.assertEqual(t.events[0]['out'], 'ok')

    def test_bad_fields_do_not_lose_the_rest_of_the_message(self):
        self.path.write_bytes(line(type='assistant', timestamp='2026-01-01T11:00:00Z', message={
            'usage': {'input_tokens': None, 'cache_read_input_tokens': 7},
            'content': ['junk', {'type': 'tool_use'}, tool_use('a', 'Read', file_path='/x/cafe.py')]}
        ).encode().replace(b'cafe', b'caf\xff'))
        t = overlay.Tail(self.path).poll()
        self.assertEqual(t.ctx, 7)
        self.assertEqual([(e['n'], e['s']) for e in t.events], [('Read', 'caf�.py')])

    def test_interrupt_closes_open_calls_and_is_not_a_prompt(self):
        self.path.write_text(
            line(type='assistant', timestamp='2026-01-01T11:00:00Z', message={'content': [tool_use('a', 'Bash', command='sleep 99')]})
            + line(type='user', timestamp='2026-01-01T11:00:05Z', message={'content': [{'type': 'text', 'text': '[Request interrupted by user for tool use]'}]})
            + line(type='user', timestamp='2026-01-01T11:00:06Z', isCompactSummary=True, message={'content': 'This session is being continued...'})
            + line(type='user', timestamp='2026-01-01T11:00:07Z', isMeta=True, message={'content': 'meta'})
            + line(type='user', timestamp='2026-01-01T11:00:08Z', message={'content': '<command-name>/clear</command-name>'})
            + line(type='user', timestamp='2026-01-01T11:00:09Z', message={'content': 'real prompt'}))
        t = overlay.Tail(self.path).poll()
        call, prompt = t.events
        self.assertIsNotNone(call['t1'])
        self.assertFalse(t.open)
        self.assertEqual(prompt['s'], 'real prompt')

    def test_wrappers_are_skipped_but_prompts_starting_with_html_are_kept(self):
        self.path.write_text(''.join(line(type='user', timestamp=f'2026-01-01T11:00:0{i}Z', message={'content': c}) for i, c in enumerate([
            '<command-name>/clear</command-name>', '<local-command-stdout>ok</local-command-stdout>',
            '<system-reminder>x</system-reminder>', '<div class="x"> why is this broken', '<b>bold</b> question'])))
        self.assertEqual([e['s'] for e in overlay.Tail(self.path).poll().events], ['<div class="x"> why is this broken', '<b>bold</b> question'])

    def test_tool_result_with_extra_text_is_not_a_prompt(self):
        self.path.write_text(
            line(type='assistant', timestamp='2026-01-01T11:00:00Z', message={'content': [tool_use('a', 'Read', file_path='/f')]})
            + line(type='user', timestamp='2026-01-01T11:00:01Z', message={'content': [tool_result('a', 'x'), {'type': 'text', 'text': 'note'}]}))
        self.assertEqual([e['n'] for e in overlay.Tail(self.path).poll().events], ['Read'])

    def test_truncated_or_replaced_file_starts_over(self):
        self.path.write_text(line(type='user', timestamp='2026-01-01T12:00:00Z', message={'content': 'a long first prompt ' * 20}))
        t = overlay.Tail(self.path).poll()
        self.path.write_text(line(type='user', timestamp='2026-01-01T12:00:01Z', message={'content': 'fresh'}))
        self.assertEqual([e['s'] for e in t.poll().events], ['fresh'])
        self.path.unlink()
        self.path.write_text(line(type='user', timestamp='2026-01-01T12:00:02Z', message={'content': 'new file, new inode, longer than before ' * 30}))
        self.assertEqual(len(t.poll().events), 1)

    def test_missing_file_is_quiet(self):
        t = overlay.Tail(self.path).poll()
        self.assertEqual((list(t.events), t.pos), ([], 0))

    def test_title_and_last_prompt(self):
        self.path.write_text(line(type='ai-title', aiTitle='Fix login') + line(type='last-prompt', lastPrompt='why?'))
        t = overlay.Tail(self.path).poll()
        self.assertEqual((t.title, t.prompt), ('Fix login', 'why?'))


class HelpersTest(unittest.TestCase):
    def test_summarize_prefers_description_and_shortens_paths(self):
        self.assertEqual(overlay.summarize({'command': 'ls', 'description': 'List'}), 'List')
        self.assertEqual(overlay.summarize({'file_path': '/a/b/c.py'}), 'c.py')
        self.assertEqual(overlay.summarize({'prompt': 'line one\nline two'}), 'line one')
        self.assertEqual(overlay.summarize('not a dict'), '')

    def test_category(self):
        cases = {'Read': 'read', 'Edit': 'write', 'Bash': 'exec', 'Agent': 'agent', 'WebFetch': 'web',
                 'mcp__claude-in-chrome__navigate': 'web', 'mcp__slack__post': 'mcp', 'Skill': 'other'}
        self.assertEqual({n: overlay.category(n) for n in cases}, cases)

    def test_detail_and_result_text_are_capped(self):
        big = 'x' * (overlay.DETAIL_CHARS + 50)
        self.assertEqual(len(overlay.detail({'command': big})), overlay.DETAIL_CHARS)
        self.assertEqual(len(overlay.result_text(big)), overlay.DETAIL_CHARS)
        self.assertEqual(overlay.result_text([{'type': 'text', 'text': 'a'}, {'type': 'image'}]), 'a\n[image]')
        self.assertEqual(overlay.result_text(None), '')


if __name__ == '__main__':
    unittest.main()
