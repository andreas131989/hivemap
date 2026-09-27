"""Malformed transcripts and session files, generated at random (fixed seed): state() must never raise."""
import json
import random
import unittest

from tests import FakeClaude, overlay

rnd = random.Random(20260927)
WEIRD = [None, True, 0, -1, 1.5, 1e308, '', 'x', '\x1b[31m', 'é中文🚀', '\n\n', [], {}, [1, 'a'], {'a': None}, '<tag>', '[Request interrupted']


def weird():
    return rnd.choice(WEIRD)


def value(depth=0):
    r = rnd.random()
    if depth > 2 or r < 0.5:
        return weird()
    if r < 0.75:
        return [value(depth + 1) for _ in range(rnd.randint(0, 3))]
    return {rnd.choice(['type', 'id', 'name', 'input', 'content', 'text', 'tool_use_id', 'is_error', 'usage', 'model']): value(depth + 1)
            for _ in range(rnd.randint(0, 4))}


def block():
    kind = rnd.choice(['tool_use', 'tool_result', 'text', 'image', None, weird()])
    b = {'type': kind}
    if rnd.random() < 0.8:
        b['id'] = rnd.choice([f'u{rnd.randint(0, 5)}', weird()])
        b['tool_use_id'] = rnd.choice([f'u{rnd.randint(0, 5)}', weird()])
        b['name'] = rnd.choice(['Bash', 'Read', 'mcp__x__y', 'Agent', weird()])
        b['input'] = rnd.choice([{'command': weird(), 'file_path': weird(), 'description': weird()}, weird()])
        b['content'] = rnd.choice([weird(), [{'type': 'text', 'text': weird()}, weird()]])
        b['text'] = weird()
        b['is_error'] = weird()
    return b if rnd.random() < 0.9 else weird()


def line():
    r = rnd.random()
    if r < 0.05:
        return rnd.choice(['not json', '{', '', '[]', 'null', '"s"', '{"type":"user"}', '\xff\xfe'])
    d = {'type': rnd.choice(['user', 'assistant', 'ai-title', 'last-prompt', 'summary', weird()]),
         'timestamp': rnd.choice(['2026-01-01T10:00:00.000Z', '2026-01-01T10:00:00Z', '2026-01-01T10:00:00.123456+00:00', 'garbage', weird()]),
         'message': rnd.choice([{'content': rnd.choice([weird(), [block() for _ in range(rnd.randint(0, 3))]]), 'model': weird(),
                                 'usage': rnd.choice([weird(), {'input_tokens': weird(), 'cache_read_input_tokens': weird()}])}, weird()]),
         'aiTitle': weird(), 'lastPrompt': weird(), 'isMeta': weird(), 'isCompactSummary': weird()}
    if rnd.random() < 0.2:
        d = value()
    return json.dumps(d, default=str)


def check_state(st):
    assert isinstance(st['sessions'], list)
    for s in st['sessions']:
        for k in ('id', 'name', 'status', 'agents', 'ctx', 'updated'):
            assert k in s, k
        assert isinstance(s['updated'], (int, float)), s['updated']
        for a in s['agents']:
            for e in a['events']:
                assert isinstance(e['n'], str) and isinstance(e['s'], str) and isinstance(e['t0'], float), e
                assert e['t1'] is None or isinstance(e['t1'], float), e
    json.dumps(st)   # the server must be able to send it




class FuzzStateTest(unittest.TestCase):
    def test_random_malformed_input_never_breaks_state(self):
        for round_ in range(60):
            with FakeClaude() as fake:
                for i in range(rnd.randint(1, 3)):
                    sid = f's{i}'
                    t = fake.session(sid, [line() + '\n' for _ in range(rnd.randint(0, 30))],
                                     status=rnd.choice(['busy', 'idle', 'waiting', weird()]),
                                     waiting_for=rnd.choice([None, 'permission prompt', weird()]), updated=rnd.choice([0, 1000, 1.5]))
                    if rnd.random() < 0.5:   # mangle the session file's fields
                        f = fake.dir / 'sessions' / f'{sid}.json'
                        d = json.loads(f.read_text())
                        for k in rnd.sample(list(d), rnd.randint(1, 3)):
                            if k != 'pid':
                                d[k] = weird()
                        f.write_text(json.dumps(d))
                    if rnd.random() < 0.3:   # a subagent with random metadata
                        sub = t.with_suffix('') / 'subagents'
                        sub.mkdir(parents=True, exist_ok=True)
                        (sub / 'agent-a1.meta.json').write_text(json.dumps(rnd.choice([
                            {'parentAgentId': weird(), 'description': weird(), 'agentType': weird(), 'toolUseId': weird()}, weird()])))
                        (sub / 'agent-a1.jsonl').write_text(''.join(line() + '\n' for _ in range(rnd.randint(0, 10))))
                for full in (False, True):
                    with self.subTest(round=round_, full=full):
                        check_state(overlay.state(full=full))


if __name__ == '__main__':
    unittest.main()
