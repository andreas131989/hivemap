#!/usr/bin/env python3
"""Terminal view of the ccmap map: session/agent tree on the left, tool calls on the right.

Tree:   ↑↓/jk select · ←→/space fold · enter jump to herdr pane · tab go to calls · f follow · q quit
Calls:  ↑↓ select · enter/→ open call (input + output) · tab/← back to tree
Call:   ↑↓/pgup/pgdn scroll · tab/←/backspace back to calls
Mouse:  click a row or a call, click a selected session to fold, wheel scrolls the side it is over.
Selection is shared with the web map through the ccmap server (when it is running).
"""
import json
import os
import re
import select
import shutil
import sys
import termios
import time
import tty
import urllib.request

import overlay

COLOR = {'read': 36, 'write': 33, 'exec': 35, 'agent': 34, 'web': 32, 'mcp': 32, 'prompt': 37, 'other': 37}
KEY = re.compile(r'\x1b\[<\d+;\d+;\d+[mM]|\x1b\[[0-9;]*[~A-Za-z]|\x1bO[A-Z]|.', re.S)
MOUSE = re.compile(r'\x1b\[<(\d+);(\d+);(\d+)M')
# Tool output can hold terminal escapes; drawing them raw would wreck the screen.
UNSAFE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)?|\x1b.?|[\x00-\x09\x0b-\x1f\x7f]')
UP, DOWN, RIGHT, LEFT = ('\x1b[A', '\x1bOA', 'k'), ('\x1b[B', '\x1bOB', 'j'), ('\x1b[C', '\x1bOC', 'l'), ('\x1b[D', '\x1bOD', 'h')
ENTER, TAB, BACK, PGUP, PGDN = ('\r', '\n'), '\t', ('\x7f', '\x08'), '\x1b[5~', '\x1b[6~'
URL = f"http://127.0.0.1:{os.environ.get('CCMAP_PORT', 7777)}"
ME = f'tui-{os.getpid()}'


def api(path, body=None):
    """Talk to the ccmap server; None when it is not running."""
    try:
        req = urllib.request.Request(URL + path, data=body and json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=0.3) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def js_num(x):
    """A float as JavaScript's String(x) writes it, so call ids match the web page's."""
    r = repr(x)
    return r[:-2] if r.endswith('.0') else r


def fit(parts, width, sel=False):
    """parts: [(sgr, text)] -> one ANSI line cut and padded to width."""
    out, left = [], width
    for sgr, text in parts:
        text = text[:left]
        left -= len(text)
        sgr = ';'.join(x for x in ('7' if sel else '', sgr) if x)
        out.append(f'\x1b[{sgr}m{text}\x1b[0m' if sgr else text)
    out.append(f"\x1b[{'7' if sel else ''}m{' ' * left}\x1b[0m")
    return ''.join(out)


def wrap(text, width):
    out = []
    for line in UNSAFE.sub('', text.expandtabs(4)).split('\n'):
        out += [line[i:i + width] for i in range(0, max(len(line), 1), width)]
    return out


def dur(e, now):
    s = (e['t1'] or now) - e['t0']
    return f'{s:.0f}s' if s < 60 else f'{s / 60:.0f}m'


def clamp(v, lo, hi):
    return max(lo, min(v, hi))


def rows(st, closed):
    """Flatten sessions and their agents: [(key, session, agent|None, depth)]."""
    out = []
    for s in st['sessions']:
        out.append(((s['id'], None), s, None, 0))
        if s['id'] not in closed:
            depth = {}
            for a in s['agents']:
                d = depth[a['id']] = depth.get(a['parent'], 0) + 1 if a['parent'] else 1
                out.append(((s['id'], a['id']), s, a, d))
    return out


def row_line(r, n, closed, width, sel):
    _, s, a, d = r
    if a is None:
        mark = {'waiting': ('1;33', ' ! '), 'busy': ('1;32', ' ● ')}.get(s['status'], ('2', ' ○ '))
        return fit([('', f"{'▸' if s['id'] in closed else '▾'} {n if n < 10 else ' '}"), mark, ('1', s['name']),
                    ('2', f"  {s['ctx'] // 1000}k")], width, sel)
    last = a['events'][-1]['n'] if a['events'] else ''
    return fit([('', '  ' * d + ('└ ' if a['parent'] else '')), ('1;34' if a['running'] else '', a['label']),
                ('2', f'  {last}')], width, sel)


def model(m):
    return (m or '?').removeprefix('claude-')


def header(s, a, width):
    if a is None:
        head = [[('1', s['name']), ('2', f"  {s['status']} · {model(s['model'])} · {s['ctx'] // 1000}k ctx · pid {s['pid']}")],
                [('2', s['cwd'].replace(os.path.expanduser('~'), '~'))]]
        if s['title']:
            head.append([('3', s['title'])])
        if s['prompt']:
            head.append([('2', 'prompt: '), ('', UNSAFE.sub('', s['prompt'].splitlines()[0]))])
    else:
        head = [[('1', a['label'])],
                [('2', f"{a['type']} · {'running' if a['running'] else 'idle'} · {model(a['model'])} · {a['ctx'] // 1000}k ctx")]]
    return head + [[('2', '─' * width)]]


def calls(head, events, now, width, height, scroll, cur):
    """Call list under the header. Returns lines, clamped scroll, index of the first call shown."""
    ev = [[('2', time.strftime('%H:%M:%S ', time.localtime(e['t0']))),
           (f"{'1;' if e['t1'] is None else ''}{COLOR[e['c']]}", f"{e['n'].removeprefix('mcp__'):<8.24} "),
           ('31' if e['err'] else '', UNSAFE.sub('', e['s'])), ('2', f'  {dur(e, now)}')] for e in events]
    room = max(1, height - len(head))
    if cur is not None:   # keep the selected call on screen
        scroll = clamp(scroll, len(ev) - cur - room, len(ev) - cur - 1)
    scroll = clamp(scroll, 0, max(0, len(ev) - room))
    start = max(0, len(ev) - scroll - room)
    shown = [fit(p, width, start + i == cur) for i, p in enumerate(ev[start:len(ev) - scroll])]
    return [fit(p, width) for p in head] + shown, scroll, start


def call_detail(e, now, width, height, scroll):
    state = 'running' if e['t1'] is None else 'error' if e['err'] else 'done'
    head = [[(f"1;{COLOR[e['c']]}", e['n']),
             ('2', f"  {state} · {time.strftime('%H:%M:%S', time.localtime(e['t0']))} · {dur(e, now)}")],
            [('2', '─' * width)]]
    body = []
    for label, key in (('input', 'in'), ('output', 'out')):
        if e['c'] == 'prompt' and key == 'out':
            continue
        body.append([('1;2', label)])
        text = e.get(key)
        empty = 'still running…' if key == 'out' and e['t1'] is None else '(none)'
        body += [[('31' if e['err'] and key == 'out' else '', line)] for line in wrap(text, width)] if text else [[('2', empty)]]
        body.append([])
    room = max(1, height - len(head))
    scroll = clamp(scroll, 0, max(0, len(body) - room))
    return [fit(p, width) for p in head + body[scroll:scroll + room]], scroll


def main():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    sys.stdout.write('\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h')
    sel, idx, top, scroll, closed, follow, msg, fetched = None, 0, 0, 0, set(), False, '', 0
    focus, cur, opened, dscroll, start, head_n = 'tree', None, None, 0, 0, 0
    seen_v, sent, adopt = 0, None, True
    try:
        while True:
            if time.time() - fetched >= 1:
                st, fetched = overlay.state(full=True), time.time()
                remote = api('/api/select')
                if remote and remote['v'] > seen_v:
                    seen_v = remote['v']
                    parts = (remote['id'] or '').split(':', 4)
                    if remote['by'] != ME and parts[0] in ('s', 'a', 't') and len(parts) in (2, 3, 5):
                        adopt, follow = True, False
                        sel, focus = (parts[1], parts[2] if len(parts) > 2 else None), 'tree'
                        closed.discard(parts[1])
                        if len(parts) == 5:
                            try:
                                focus, opened, dscroll = 'detail', (float(parts[3]), parts[4]), 0
                            except ValueError:
                                pass
            if follow and st['sessions']:
                s = st['sessions'][0]
                sel = (s['id'], max(s['agents'], key=lambda a: a['last'] or 0)['id'])
                closed.discard(s['id'])
            rs = rows(st, closed)
            keys = [r[0] for r in rs]
            idx = keys.index(sel) if sel in keys else clamp(idx, 0, len(rs) - 1)
            sel = keys[idx] if keys else None
            events = ((rs[idx][2] or rs[idx][1]['agents'][0])['events']) if rs else []
            if cur is not None:
                cur = clamp(cur, 0, len(events) - 1) if events else None
            detail = next((e for e in events if (e['t0'], e['n']) == opened), None) if focus == 'detail' else None
            if focus == 'detail' and detail is None:   # the call scrolled out of history
                focus = 'events' if cur is not None else 'tree'

            if rs:   # share what is selected here with the web map
                (sid, aid), agent = keys[idx], rs[idx][2] or rs[idx][1]['agents'][0]
                lid = (f"t:{sid}:{agent['id']}:{js_num(detail['t0'])}:{detail['n']}" if detail
                       else f's:{sid}' if aid in (None, 'main') else f'a:{sid}:{aid}')
                if adopt:
                    sent, adopt = lid, False
                elif lid != sent:
                    r = api('/api/select', {'id': lid, 'by': ME})
                    sent, seen_v = lid, max(seen_v, r['v'] if r else 0)

            w, h = shutil.get_terminal_size()
            lw, rw, body = min(44, w // 3), w - min(44, w // 3) - 1, h - 1
            top = min(max(top, idx - body + 1), idx, max(0, len(rs) - body)) if rs else 0
            nums = {s['id']: n for n, s in enumerate(st['sessions'], 1)}
            left = [row_line(r, nums[r[1]['id']], closed, lw, i == idx)
                    for i, r in enumerate(rs[top:top + body], top)]
            if not rs:
                right, head_n = [fit([('2', 'no running Claude Code sessions')], rw)], 0
            elif detail:
                right, dscroll = call_detail(detail, st['now'], rw, body, dscroll)
            else:
                head = header(rs[idx][1], rs[idx][2], rw)
                head_n = len(head)
                right, scroll, start = calls(head, events, st['now'], rw, body, scroll, cur if focus == 'events' else None)
            screen = [(left[i] if i < len(left) else ' ' * lw) + '\x1b[2m│\x1b[0m' + (right[i] if i < len(right) else '\x1b[K')
                      for i in range(body)]
            foot = msg or {'tree': '↑↓ select · ←→ fold · enter jump · tab calls · f follow' + (' [on]' if follow else '') + ' · q quit',
                           'events': '↑↓ select call · enter open · tab/← back · q quit',
                           'detail': '↑↓ pgup/pgdn scroll · ←/tab back · q quit'}[focus]
            sys.stdout.write('\x1b[H' + '\r\n'.join(screen) + f'\r\n\x1b[2m{foot[:w - 1]}\x1b[0m\x1b[K')
            sys.stdout.flush()

            if not select.select([fd], [], [], max(0.05, 1 - (time.time() - fetched)))[0]:
                continue
            msg = ''
            for k in KEY.findall(os.read(fd, 256).decode(errors='ignore')):
                row = rs[idx] if rs else None
                m = MOUSE.match(k)
                if k == 'q':
                    return
                elif m:
                    b, x, y = map(int, m.groups())
                    step = -1 if b == 64 else 1
                    if b in (64, 65) and x > lw:
                        if focus == 'detail':
                            dscroll += 3 * step
                        else:
                            scroll -= 3 * step
                    elif b in (64, 65):
                        idx, follow, scroll, cur, focus = idx + step, False, 0, None, 'tree'
                    elif b == 0 and x <= lw and top + y - 1 < len(rs):
                        hit = top + y - 1
                        if hit == idx and rs[hit][2] is None:
                            closed ^= {rs[hit][1]['id']}
                        idx, follow, scroll, cur, focus = hit, False, 0, None, 'tree'
                    elif b == 0 and x > lw and focus != 'detail' and rs and 0 <= y - 1 - head_n < len(right) - head_n:
                        cur = start + y - 1 - head_n
                        focus, opened, dscroll, follow = 'detail', (events[cur]['t0'], events[cur]['n']), 0, False
                elif focus == 'detail':
                    if k in UP or k in DOWN:
                        dscroll += -1 if k in UP else 1
                    elif k in (PGUP, PGDN):
                        dscroll += (body - 3) * (-1 if k == PGUP else 1)
                    elif k in LEFT or k == TAB or k in BACK:
                        focus = 'events'
                elif focus == 'events':
                    if (k in UP or k in DOWN) and events:
                        cur = clamp((len(events) if cur is None else cur) + (-1 if k in UP else 1), 0, len(events) - 1)
                    elif k in (PGUP, PGDN) and events:
                        cur = clamp(cur + (body - 6) * (-1 if k == PGUP else 1), 0, len(events) - 1)
                    elif (k in ENTER or k in RIGHT) and cur is not None:
                        focus, opened, dscroll = 'detail', (events[cur]['t0'], events[cur]['n']), 0
                    elif k in LEFT or k == TAB or k in BACK:
                        focus, cur, scroll = 'tree', None, 0
                elif k in UP or k in DOWN:
                    idx, follow, scroll = idx + (-1 if k in UP else 1), False, 0
                elif k == TAB and events:
                    focus, cur, follow = 'events', len(events) - 1, False
                elif k in RIGHT and row:
                    closed.discard(row[1]['id'])
                elif k in LEFT and row:
                    closed.add(row[1]['id'])
                    idx = keys.index((row[1]['id'], None))
                elif k == ' ' and row:
                    closed ^= {row[1]['id']}
                elif k in (PGUP, PGDN):
                    scroll += (body - 6) * (1 if k == PGUP else -1)
                elif k == 'f':
                    follow = not follow
                elif (k in ENTER and row) or (k.isdigit() and 0 < int(k) <= len(st['sessions'])):
                    s = row[1] if k in ENTER else st['sessions'][int(k) - 1]
                    try:
                        msg = f"jumped to {s['name']}" if st['herdr'] and overlay.herdr_focus(s['pid']) else f"no herdr pane for {s['name']}"
                    except Exception as err:   # herdr missing/slow: show it, keep the view alive
                        msg = f'herdr: {err}'
                idx = clamp(idx, 0, len(keys) - 1)
                sel = keys[idx] if keys else None
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write('\x1b[?1000l\x1b[?1006l\x1b[?25h\x1b[?1049l')
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


if __name__ == '__main__':
    main()
