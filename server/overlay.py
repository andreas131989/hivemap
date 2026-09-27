#!/usr/bin/env python3
"""Live map of running Claude Code sessions and their agents.

Started by `hivemap` (bin/hivemap); serves http://127.0.0.1:$HIVEMAP_PORT (default 7777)
"""
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

CLAUDE = Path.home() / '.claude'
HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get('HIVEMAP_PORT', 7777))
KEEP_S = 3600          # history sent to the page
AGENT_QUIET_S = 12     # a subagent whose file changed this recently counts as running
DETAIL_CHARS = 4000    # per call input/output kept for the terminal view's detail pane


def as_str(v, default=''):
    """v if it is a string, else default: Claude Code's files are read, never trusted to have the expected types."""
    return v if isinstance(v, str) else default


def ts(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()


def summarize(inp):
    if not isinstance(inp, dict):
        return ''
    for k in ('description', 'file_path', 'pattern', 'url', 'query', 'command', 'prompt', 'skill', 'action'):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            if k == 'file_path':
                v = os.path.basename(v)
            return v.strip().splitlines()[0][:140]
    return ''


def detail(inp):
    """Full tool input as readable 'key: value' lines."""
    lines = []
    for k, v in inp.items():
        v = v if isinstance(v, str) else json.dumps(v)
        lines.append(f'{k}:\n{v}' if '\n' in v else f'{k}: {v}')
    return '\n'.join(lines)[:DETAIL_CHARS]


def result_text(c):
    if isinstance(c, list):
        c = '\n'.join(b.get('text', '') if b.get('type') == 'text' else f"[{b.get('type')}]" for b in c if isinstance(b, dict))
    return c[:DETAIL_CHARS] if isinstance(c, str) else ''


def category(name):
    if name in ('Read', 'Grep', 'Glob', 'LS', 'ToolSearch'):
        return 'read'
    if name in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
        return 'write'
    if name in ('Bash', 'BashOutput', 'KillShell', 'Monitor'):
        return 'exec'
    if name in ('Agent', 'Task', 'SendMessage'):
        return 'agent'
    if name in ('WebFetch', 'WebSearch') or 'chrome' in name.lower():
        return 'web'
    if name.startswith('mcp__'):
        return 'mcp'
    return 'other'


class Tail:
    """Incrementally parses one transcript .jsonl file."""

    def __init__(self, path, ino=None):
        self.path, self.pos, self.buf, self.ino = path, 0, b'', ino
        self.events = deque(maxlen=500)
        self.open = {}
        self.title = self.prompt = self.model = self.last_t = None
        self.ctx = 0
        self.mtime = 0

    def poll(self):
        try:
            with open(self.path, 'rb') as f:
                st = os.fstat(f.fileno())
                if st.st_ino != self.ino or st.st_size < self.pos:   # new, replaced or truncated: start over
                    self.__init__(self.path, st.st_ino)
                self.mtime = st.st_mtime
                f.seek(self.pos)
                for raw in f:   # line by line, so a huge transcript is never held in memory at once
                    if not raw.endswith(b'\n'):   # still being written; finish it next poll
                        self.buf += raw
                        break
                    line, self.buf = self.buf + raw, b''
                    try:
                        self.feed(json.loads(line.decode('utf-8', 'replace')))
                    except (ValueError, KeyError, TypeError, AttributeError):
                        pass
                self.pos = f.tell()
        except OSError:
            pass
        return self

    def feed(self, d):
        kind = d.get('type')
        if kind == 'ai-title':
            self.title = as_str(d.get('aiTitle'), self.title)
        elif kind == 'last-prompt':
            self.prompt = as_str(d.get('lastPrompt'), self.prompt)
        if kind not in ('assistant', 'user'):
            return
        when = ts(d['timestamp'])
        self.last_t = when
        msg = d.get('message') or {}
        content = msg.get('content')
        if kind == 'assistant':
            self.model = as_str(msg.get('model')) or self.model
            u = msg.get('usage') if isinstance(msg.get('usage'), dict) else {}
            ctx = sum(v for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
                      if isinstance(v := u.get(k), (int, float)) and not isinstance(v, bool))
            if ctx:
                self.ctx = ctx
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get('type') == 'tool_use' and isinstance(b.get('id'), str) and isinstance(b.get('name'), str):
                    inp = b.get('input') if isinstance(b.get('input'), dict) else {}
                    e = {'id': b['id'], 'n': b['name'], 's': summarize(inp), 'c': category(b['name']),
                         'f': as_str(inp.get('file_path')) or as_str(inp.get('notebook_path')) or None, 't0': when, 't1': None, 'err': False, 'in': detail(inp)}
                    self.events.append(e)
                    self.open[b['id']] = e
            return
        blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else (content or [])
        texts, results = [], False
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get('type') == 'tool_result':
                results = True
                e = self.open.pop(b.get('tool_use_id'), None)
                if e:
                    e['t1'], e['err'], e['out'] = when, bool(b.get('is_error')), result_text(b.get('content'))
            elif b.get('type') == 'text':
                texts.append(b.get('text', ''))
        text = ' '.join(t for t in texts if isinstance(t, str)).strip()
        if text.startswith('[Request interrupted'):   # whatever was running died with the interrupt
            for e in self.open.values():
                e['t1'] = when
            self.open.clear()
            return
        # Claude Code's own wrappers (<command-name>, <local-command-stdout>, <system-reminder>, ...) all use hyphenated
        # tags; a prompt that merely starts with pasted HTML like <div> is still the user's prompt
        wrapper = re.match(r'<[a-z]+(-[a-z]+)+>', text)
        if text and not results and not wrapper and not d.get('isMeta') and not d.get('isCompactSummary'):
            self.events.append({'n': 'Prompt', 's': text.splitlines()[0][:140], 'c': 'prompt', 't0': when, 't1': when, 'err': False,
                                'in': text[:DETAIL_CHARS]})


tails, paths = {}, {}
lock = threading.Lock()


def tail(path):
    t = tails.get(path)
    if t is None:
        t = tails[path] = Tail(path)
    return t.poll()


def alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def agent_json(aid, parent, label, kind, t, running, now, full):
    events = []
    for e in t.events:
        if (e['t1'] or now) < now - KEEP_S:
            continue
        e = {k: v for k, v in e.items() if k != 'id' and (full or k not in ('in', 'out'))}
        if e['t1'] is None and not running:   # interrupted or abandoned call
            e['t1'] = max(t.last_t or e['t0'], e['t0'])
        events.append(e)
    return {'id': aid, 'parent': parent, 'label': label, 'type': kind, 'running': running,
            'ctx': t.ctx, 'model': t.model, 'last': t.last_t, 'events': events}


def session_json(s, now, full):
    sid = s['sessionId']
    path = paths.get(sid)
    if path is None:
        path = paths[sid] = next(CLAUDE.glob(f'projects/*/{sid}.jsonl'), None)
    if path is None:
        return None
    main = tail(path)
    status = s.get('status') if s.get('status') in ('busy', 'idle', 'waiting') else 'idle'   # and why it waits, in waitingFor
    busy = status in ('busy', 'waiting')   # a waiting session's pending call is still open, not ended
    agents = [agent_json('main', None, 'main', 'main', main, busy, now, full)]

    subs = []
    for m in (path.with_suffix('') / 'subagents').glob('agent-*.meta.json'):
        aid = m.name[len('agent-'):-len('.meta.json')]
        try:
            meta = json.loads(m.read_text())
        except (OSError, ValueError):
            meta = {}
        meta = {k: as_str(meta.get(k)) for k in ('toolUseId', 'parentAgentId', 'description', 'agentType')} if isinstance(meta, dict) else {}
        subs.append((aid, meta, tail(m.with_name(f'agent-{aid}.jsonl'))))
    open_ids = set(main.open).union(*(t.open for _, _, t in subs))
    ids = {aid for aid, _, _ in subs}
    for aid, meta, t in subs:
        running = busy and (meta.get('toolUseId') in open_ids or now - t.mtime < AGENT_QUIET_S)
        if not running and (t.last_t or 0) < now - KEEP_S:
            continue
        parent = meta.get('parentAgentId')
        agents.append(agent_json(aid, parent if parent in ids else 'main',
                                 meta.get('description') or aid, meta.get('agentType') or 'agent', t, running, now, full))
    updated = s.get('updatedAt')
    return {'id': sid, 'pid': s.get('pid'), 'name': as_str(s.get('name')) or sid[:8], 'cwd': as_str(s.get('cwd')), 'status': status,
            'waiting_for': as_str(s.get('waitingFor')) or None if status == 'waiting' else None,
            'kind': as_str(s.get('kind')) or None, 'title': main.title, 'prompt': main.prompt, 'model': main.model,
            'ctx': main.ctx, 'updated': updated / 1000 if isinstance(updated, (int, float)) and not isinstance(updated, bool) else 0,
            'agents': agents}


def state(full=False):
    """full: include each call's input/output (the terminal view wants them, the polling web page doesn't)."""
    now = time.time()
    out = []
    # ponytail: one global lock, fine for a single local viewer
    with lock:
        for f in (CLAUDE / 'sessions').glob('*.json'):
            try:
                s = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(s, dict) and as_str(s.get('sessionId')) and alive(s.get('pid')):
                j = session_json(s, now, full)
                if j:
                    out.append(j)
        # forget sessions that have ended, so a server left running for weeks doesn't keep growing
        live = {j['id'] for j in out}
        for sid in [sid for sid in paths if sid not in live]:
            del paths[sid]
        kept = [p.with_suffix('') for p in paths.values() if p]   # a live transcript, minus .jsonl = its subagent dir
        for p in [p for p in tails if not any(p.with_suffix('') == k or k in p.parents for k in kept)]:
            del tails[p]
    out.sort(key=lambda j: ({'waiting': 0, 'busy': 1}.get(j['status'], 2), -j['updated']))
    return {'now': now, 'jump': bool(HERDR or TMUX), 'sessions': out}


HERDR, TMUX = shutil.which('herdr'), shutil.which('tmux')
# Selection shared by the web page and the terminal view: a node id ('s:<sid>', 'a:<sid>:<aid>',
# 't:<sid>:<aid>:<t0>:<name>' or 'f:<path>'), who set it, and a counter so each side only applies news.
SEL = {'id': None, 'by': '', 'v': 0}


def call_detail(cid):
    """Full input/output of one call, by its 't:' node id."""
    try:
        _, sid, aid, t0, name = cid.split(':', 4)
        t0 = float(t0)
    except ValueError:
        return None
    path = paths.get(sid)
    if path is None or not re.fullmatch(r'[\w-]+', aid):
        return None
    if aid != 'main':
        path = path.with_suffix('') / 'subagents' / f'agent-{aid}.jsonl'
    with lock:
        t = tails.get(path)
        e = t and next((e for e in t.events if e['t0'] == t0 and e['n'] == name), None)
        return e and {'in': e.get('in', ''), 'out': e.get('out'), 'done': e['t1'] is not None}


def herdr(*args):
    r = subprocess.run([HERDR, *args], capture_output=True, text=True, timeout=5)
    return json.loads(r.stdout or '{}').get('result', {})


panes = {}   # Claude Code pid -> (herdr pane id or None, the pane set it was looked up against)


def claude_panes():
    """herdr panes running Claude Code."""
    return {a['pane_id'] for a in herdr('agent', 'list').get('agents', []) if a.get('agent') == 'claude'}


def herdr_pane(pid, agents):
    """The herdr pane whose foreground process is this Claude Code pid; asked of herdr only when the panes change."""
    key = frozenset(agents)
    hit = panes.get(pid)
    if hit and (hit[0] in agents or hit[1] == key):
        return hit[0] if hit[0] in agents else None
    found = None
    for pane in agents:
        info = herdr('pane', 'process-info', '--pane', pane).get('process_info', {})
        if pid == info.get('foreground_process_group_id') or any(p.get('pid') == pid for p in info.get('foreground_processes', [])):
            found = pane
            break
    panes[pid] = (found, key)
    return found


def herdr_focus(pid):
    """Focus the herdr pane running this Claude Code pid. Returns the pane id or None."""
    pane = herdr_pane(pid, claude_panes())
    if pane:
        subprocess.run([HERDR, 'agent', 'focus', pane], capture_output=True, timeout=5, check=True)
    return pane


def ancestors(pid):
    """pid and its parents up to init (ps works on Linux and macOS)."""
    chain = []
    while pid > 1 and pid not in chain and len(chain) < 50:
        chain.append(pid)
        out = subprocess.run(['ps', '-o', 'ppid=', '-p', str(pid)], capture_output=True, text=True, timeout=5).stdout.strip()
        pid = int(out) if out.isdigit() else 0
    return chain


def tmux_focus(pid):
    """Select the tmux pane whose shell started this Claude Code pid. Returns the pane id or None."""
    listing = subprocess.run([TMUX, 'list-panes', '-a', '-F', '#{pane_id} #{pane_pid}'], capture_output=True, text=True, timeout=5)
    shells = dict(reversed(line.split()) for line in listing.stdout.splitlines() if len(line.split()) == 2)
    pane = next((shells[str(p)] for p in ancestors(pid) if str(p) in shells), None)
    if pane:
        for cmd in ('switch-client', 'select-window', 'select-pane'):   # switch-client fails with no attached client; fine
            subprocess.run([TMUX, cmd, '-t', pane], capture_output=True, timeout=5)
    return pane


def focus(pid):
    """Bring the terminal pane running this Claude Code session to the front: herdr first, then tmux."""
    if HERDR:
        try:
            pane = herdr_focus(pid)
            if pane:
                return pane
        except (OSError, ValueError, subprocess.SubprocessError):   # herdr installed but not running, or answering oddly
            pass
    return (TMUX and tmux_focus(pid)) or None


def live_pids():
    pids = set()
    for f in (CLAUDE / 'sessions').glob('*.json'):
        try:
            pids.add(json.loads(f.read_text()).get('pid'))
        except (OSError, ValueError):
            pass
    return pids


def notify(text):
    """Desktop notification: notify-send on Linux, osascript on macOS; silently nothing elsewhere."""
    if shutil.which('notify-send'):
        cmd = ['notify-send', '--app-name=hivemap', '--', 'hivemap', text]   # '--': a name starting with '-' isn't an option
    elif shutil.which('osascript'):   # passed as an argument: no AppleScript quoting, so any name shows as written
        cmd = ['osascript', '-e', 'on run argv', '-e', 'display notification (item 1 of argv) with title "hivemap"',
               '-e', 'end run', text]
    else:
        return
    subprocess.run(cmd, capture_output=True, timeout=5)


FINISH_AFTER_S = 30   # a turn shorter than this isn't worth a "finished" notification


def news(sessions, before, now):
    """Status changes worth a notification. before/after: {session id: (status, since)}. Returns (after, messages)."""
    after, messages = {}, []
    for j in sessions:
        old, since = before.get(j['id'], (None, now))
        if j['status'] != old:
            if j['status'] == 'waiting':
                messages.append(f"{j['name']} is waiting on you" + (f" ({j['waiting_for']})" if j.get('waiting_for') else ''))
            elif j['status'] == 'idle' and old == 'busy' and now - since >= FINISH_AFTER_S:
                messages.append(f"{j['name']} finished")
            since = now
        after[j['id']] = (j['status'], since)
    return after, messages


def watch(every=2):
    """Desktop notifications when a session starts waiting on you, or finishes a real piece of work."""
    seen = {}
    while True:
        try:
            seen, messages = news(state()['sessions'], seen, time.time())
            for m in messages:
                notify(m)
        except Exception as err:   # never let the watcher die; the log says why it hiccuped
            print(f'watch: {err!r}', flush=True)
        time.sleep(every)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError):   # a closed tab mid-response is normal
            super().handle_error(request, client_address)

    def server_bind(self):
        # HTTPServer.server_bind also looks up the host's FQDN, which can take ~30s on macOS before the socket
        # starts listening. Nothing here uses the name.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class Handler(BaseHTTPRequestHandler):
    timeout = 10   # a client that stalls mid-request gives up its thread

    def allowed(self):
        # Transcripts are private: refuse DNS-rebound hosts, serve only to this machine's loopback names.
        if self.headers.get('Host') in (f'127.0.0.1:{PORT}', f'localhost:{PORT}'):
            return True
        self.send_error(403)
        return False

    def reply(self, body, ctype='application/json', code=200):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        # no framing by other sites (a framed page could be clicked through to "Jump to terminal pane")
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed():
            return
        url = urlsplit(self.path)
        if url.path == '/api/state':
            self.reply(json.dumps({**state(), 'sel': SEL}).encode())
        elif url.path == '/api/select':
            self.reply(json.dumps(SEL).encode())
        elif url.path == '/api/call':
            d = call_detail(parse_qs(url.query).get('id', [''])[0])
            self.reply(json.dumps(d).encode(), code=200 if d else 404)
        elif self.path == '/':
            self.reply((HERE / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        else:
            self.send_error(404)

    def do_POST(self):
        if not self.allowed():
            return
        # A JSON content type can't be sent cross-origin without a CORS preflight, which this server never grants.
        if self.headers.get('Content-Type') != 'application/json' or self.path not in ('/api/focus', '/api/select'):
            self.send_error(404)
            return
        try:
            body = json.loads(self.rfile.read(max(0, min(int(self.headers.get('Content-Length', 0)), 2000))))
        except (ValueError, RecursionError):   # older Pythons give up on ~1000-deep JSON with RecursionError
            body = None
        body = body if isinstance(body, dict) else {}
        if self.path == '/api/select':
            global SEL
            nid, by = body.get('id'), body.get('by')
            if not (nid is None or isinstance(nid, str) and len(nid) < 1000) or not isinstance(by, str) or len(by) > 64:
                self.reply(b'{"error":"bad selection"}', code=400)
                return
            with lock:
                SEL = {'id': nid, 'by': by, 'v': SEL['v'] + 1}
            self.reply(json.dumps(SEL).encode())
            return
        if not (HERDR or TMUX):
            self.send_error(404)
            return
        pid = body.get('pid')
        if not isinstance(pid, int) or pid not in live_pids():
            self.reply(b'{"error":"unknown session"}', code=400)
            return
        try:
            pane = focus(pid)
        except (OSError, ValueError, subprocess.SubprocessError):
            pane = None
        self.reply(json.dumps({'pane': pane}).encode(), code=200 if pane else 404)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    if len(sys.argv) > 1:   # the launcher passes the port, so `hivemap stop` can tell instances apart
        PORT = int(sys.argv[1])
    httpd = Server(('127.0.0.1', PORT), Handler)
    print(f'hivemap on http://127.0.0.1:{PORT}', flush=True)
    if os.environ.get('HIVEMAP_NOTIFY', '1') != '0':
        threading.Thread(target=watch, daemon=True).start()
    httpd.serve_forever()
