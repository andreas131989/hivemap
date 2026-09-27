#!/usr/bin/env python3
"""Self-check for the transcript parser and the call ids both views share. Run: python3 server/test_overlay.py"""
import json
import tempfile
from pathlib import Path

import overlay
from tui import js_num, split_keys


def line(**d):
    return json.dumps(d) + '\n'


def main():
    tmp = Path(tempfile.mkdtemp())
    path = tmp / 'sid1.jsonl'
    use = {'type': 'tool_use', 'id': 'tu1', 'name': 'Bash', 'input': {'command': 'ls\n-la', 'description': 'List files'}}
    path.write_text(
        line(type='user', timestamp='2026-01-01T10:00:00.000Z', message={'content': 'fix the bug'})
        + 'not json\n'
        + line(type='assistant', timestamp='2026-01-01T10:00:01.250Z',
               message={'model': 'claude-x', 'usage': {'input_tokens': 5, 'cache_read_input_tokens': 995}, 'content': [use]})
        + line(type='user', timestamp='2026-01-01T10:00:03.000Z', message={'content': [
            {'type': 'tool_result', 'tool_use_id': 'tu1', 'is_error': True, 'content': [{'type': 'text', 'text': 'boom'}]}]})[:40])

    t = overlay.Tail(path).poll()
    prompt, call = t.events
    assert (prompt['n'], prompt['s'], prompt['in']) == ('Prompt', 'fix the bug', 'fix the bug')
    assert (call['n'], call['s'], call['c'], call['t1']) == ('Bash', 'List files', 'exec', None), call
    assert call['in'] == 'command:\nls\n-la\ndescription: List files'
    assert (t.model, t.ctx) == ('claude-x', 1000)
    assert 'tu1' in t.open, 'a call with no result yet stays open'

    # the rest of the half-written line arrives: the call closes with its output and error flag
    with open(path, 'a') as f:
        f.write(line(type='user', timestamp='2026-01-01T10:00:03.000Z', message={'content': [
            {'type': 'tool_result', 'tool_use_id': 'tu1', 'is_error': True, 'content': [{'type': 'text', 'text': 'boom'}]}]})[40:])
    t.poll()
    assert (call['out'], call['err'], call['t1'] - call['t0']) == ('boom', True, 1.75), call
    assert not t.open

    # call ids: the page writes t0 with JS String(), the terminal with js_num(); the server must resolve both
    assert js_num(1767261601.25) == '1767261601.25' and js_num(1767261600.0) == '1767261600'
    overlay.paths['sid1'], overlay.tails[path] = path, t
    cid = f"t:sid1:main:{js_num(call['t0'])}:Bash"
    assert overlay.call_detail(cid) == {'in': call['in'], 'out': 'boom', 'done': True}
    assert overlay.call_detail('t:sid1:../../etc:1:Bash') is None, 'agent ids are never paths'
    assert overlay.call_detail('t:nope:main:1:Bash') is None and overlay.call_detail('garbage') is None

    # the web payload leaves the heavy fields out, the terminal view gets them
    lite = overlay.agent_json('main', None, 'main', 'main', t, False, call['t1'], False)['events'][1]
    full = overlay.agent_json('main', None, 'main', 'main', t, False, call['t1'], True)['events'][1]
    assert 'in' not in lite and 'out' not in lite and 'id' not in lite and full['out'] == 'boom'

    # messy input: null usage, a junk block, bad UTF-8, then an interrupt and a compaction summary
    p2 = tmp / 'sid2.jsonl'
    p2.write_bytes((
        line(type='assistant', timestamp='2026-01-01T11:00:00Z', message={'usage': {'input_tokens': None, 'cache_read_input_tokens': 7},
             'content': ['junk', {'type': 'tool_use', 'id': 'a', 'name': 'Read', 'input': {'file_path': '/x/café.py'}}]})
        + line(type='user', timestamp='2026-01-01T11:00:05Z', message={'content': [{'type': 'text', 'text': '[Request interrupted by user for tool use]'}]})
        + line(type='user', timestamp='2026-01-01T11:00:06Z', isCompactSummary=True, message={'content': 'This session is being continued...'})
        + line(type='user', timestamp='2026-01-01T11:00:07Z', message={'content': 'real prompt'})
    ).encode().replace(b'caf\\u00e9', b'caf\xff'))
    t2 = overlay.Tail(p2).poll()
    read, prompt = t2.events
    assert t2.ctx == 7 and read['n'] == 'Read' and read['s'] == 'caf�.py', read
    assert read['t1'] is not None and not t2.open, 'an interrupt closes what was running'
    assert prompt['s'] == 'real prompt', 'interrupt notices and compaction summaries are not prompts'

    # the transcript is replaced by a shorter file: start over instead of going quiet
    p2.write_text(line(type='user', timestamp='2026-01-01T12:00:00Z', message={'content': 'fresh'}))
    assert [e['s'] for e in t2.poll().events] == ['fresh']

    # a mouse event cut in half by a read is kept whole for the next read, not typed as digits
    keys, rest = split_keys('j\x1b[<65;30;1')
    assert (keys, rest) == (['j'], '\x1b[<65;30;1')
    assert split_keys(rest + '0M') == (['\x1b[<65;30;10M'], '')
    assert split_keys('\x1b[A\x1bO') == (['\x1b[A'], '\x1bO')
    # one notification per wait: when it starts, not while it lasts
    seen, told = set(), []
    for status in ('idle', 'waiting', 'waiting', 'busy', 'waiting'):
        seen, started = overlay.new_waits([{'id': 's1', 'name': 'proj', 'status': status}], seen)
        told += started
    assert told == ['proj', 'proj'], told

    # 'hide finished' drops done subagents but keeps the session and its main agent
    agent = lambda aid, parent, running: {'id': aid, 'parent': parent, 'running': running}
    st = {'sessions': [{'id': 's', 'agents': [agent('main', None, False), agent('x', 'main', True), agent('y', 'main', False)]}]}
    from tui import rows
    assert [r[0] for r in rows(st, set(), hide_done=True)] == [('s', None), ('s', 'main'), ('s', 'x')]
    assert len(rows(st, set())) == 4
    print('ok')


if __name__ == '__main__':
    main()
