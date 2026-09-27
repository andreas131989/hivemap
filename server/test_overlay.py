#!/usr/bin/env python3
"""Self-check for the transcript parser and the call ids both views share. Run: python3 server/test_overlay.py"""
import json
import tempfile
from pathlib import Path

import overlay
from tui import js_num


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
    print('ok')


if __name__ == '__main__':
    main()
