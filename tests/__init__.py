"""hivemap tests. Run from the repo root: python3 -m unittest -v"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

import overlay  # noqa: E402


def line(**d):
    """One transcript line."""
    return json.dumps(d) + '\n'


def stamp(seconds_ago=0):
    """An ISO timestamp like Claude Code writes, `seconds_ago` before now."""
    t = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return t.strftime('%Y-%m-%dT%H:%M:%S.') + f'{t.microsecond // 1000:03d}Z'


def tool_use(uid, name, **inp):
    return {'type': 'tool_use', 'id': uid, 'name': name, 'input': inp}


def tool_result(uid, text, error=False):
    return {'type': 'tool_result', 'tool_use_id': uid, 'is_error': error, 'content': [{'type': 'text', 'text': text}]}


class FakeClaude:
    """A throwaway ~/.claude that overlay reads instead of the real one. Use as a context manager."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / '.claude'
        (self.dir / 'sessions').mkdir(parents=True)
        self.saved = overlay.CLAUDE, overlay.HERDR, overlay.TMUX
        overlay.CLAUDE, overlay.HERDR, overlay.TMUX = self.dir, None, None
        overlay.tails.clear(), overlay.paths.clear(), overlay.panes.clear()
        overlay.SEL = {'id': None, 'by': '', 'v': 0}
        return self

    def __exit__(self, *exc):
        overlay.CLAUDE, overlay.HERDR, overlay.TMUX = self.saved
        overlay.tails.clear(), overlay.paths.clear(), overlay.panes.clear()
        self.tmp.cleanup()

    def session(self, sid, lines, name=None, status='idle', pid=None, updated=0, subagents=(), waiting_for=None):
        """Write a live session (its pid defaults to this test process, so it counts as running)."""
        (self.dir / 'sessions' / f'{sid}.json').write_text(json.dumps({
            'sessionId': sid, 'pid': pid or os.getpid(), 'name': name or sid, 'cwd': '/work/' + sid,
            'status': status, 'kind': 'interactive', 'updatedAt': updated,
            **({'waitingFor': waiting_for} if waiting_for else {})}))
        transcript = self.dir / 'projects' / '-work' / f'{sid}.jsonl'
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(''.join(lines))
        for aid, meta, sub_lines in subagents:
            base = transcript.with_suffix('') / 'subagents'
            base.mkdir(parents=True, exist_ok=True)
            (base / f'agent-{aid}.meta.json').write_text(json.dumps(meta))
            (base / f'agent-{aid}.jsonl').write_text(''.join(sub_lines))
        return transcript
