"""The local HTTP server, exercised over real sockets."""
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import quote

from tests import FakeClaude, line, overlay, stamp, tool_result, tool_use
from tui import js_num


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeClaude().__enter__()
        cls.fake.session('s1', [
            line(type='assistant', timestamp=stamp(5), message={'content': [tool_use('t1', 'Bash', command='make')]}),
            line(type='user', timestamp=stamp(4), message={'content': [tool_result('t1', 'built')]}),
        ], name='proj')
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), overlay.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.saved_port, overlay.PORT = overlay.PORT, cls.port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        overlay.PORT = cls.saved_port
        cls.fake.__exit__(None, None, None)

    def request(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        h = {'Host': host or f'127.0.0.1:{self.port}', **(headers or {})}
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def post(self, path, obj, ctype='application/json'):
        return self.request('POST', path, json.dumps(obj), {'Content-Type': ctype})

    def test_page_is_served_and_cannot_be_framed(self):
        r, body = self.request('GET', '/')
        self.assertEqual(r.status, 200)
        self.assertIn(b'<title>hivemap</title>', body)
        self.assertEqual(r.getheader('X-Frame-Options'), 'DENY')
        self.assertIn("frame-ancestors 'none'", r.getheader('Content-Security-Policy'))

    def test_foreign_host_is_refused(self):
        for host in ('evil.example:%d' % self.port, 'attacker.test', f'127.0.0.1:{self.port + 1}'):
            self.assertEqual(self.request('GET', '/api/state', host=host)[0].status, 403, host)

    def test_localhost_name_is_allowed(self):
        self.assertEqual(self.request('GET', '/api/state', host=f'localhost:{self.port}')[0].status, 200)

    def test_state_includes_sessions_and_selection(self):
        r, body = self.request('GET', '/api/state')
        st = json.loads(body)
        self.assertEqual([s['name'] for s in st['sessions']], ['proj'])
        self.assertIn('v', st['sel'])

    def test_selection_round_trip(self):
        r, body = self.post('/api/select', {'id': 's:s1', 'by': 'test'})
        v = json.loads(body)['v']
        self.assertEqual(json.loads(self.request('GET', '/api/select')[1]), {'id': 's:s1', 'by': 'test', 'v': v})
        self.assertEqual(self.post('/api/select', {'id': 's:s2', 'by': 'test'})[0].status, 200)
        self.assertEqual(json.loads(self.request('GET', '/api/select')[1])['v'], v + 1)

    def test_bad_selections_are_rejected(self):
        for body in ({'id': 5, 'by': 'x'}, {'id': 's:1', 'by': 5}, {'id': 'x' * 2000, 'by': 'x'}, {'id': 's:1', 'by': 'b' * 100}):
            self.assertEqual(self.post('/api/select', body)[0].status, 400, body)

    def test_posts_need_json_content_type(self):
        self.assertEqual(self.post('/api/select', {'id': 's:1', 'by': 'x'}, ctype='text/plain')[0].status, 404)

    def test_negative_or_bogus_content_length_does_not_hang(self):
        for length in ('-1', 'abc'):
            r, _ = self.request('POST', '/api/select', None, {'Content-Type': 'application/json', 'Content-Length': length})
            self.assertEqual(r.status, 400, length)

    def test_call_details(self):
        self.request('GET', '/api/state')   # the server learns transcripts by polling state
        (e,) = overlay.state()['sessions'][0]['agents'][0]['events']
        cid = f"t:s1:main:{js_num(e['t0'])}:Bash"
        r, body = self.request('GET', '/api/call?id=' + quote(cid))
        self.assertEqual((r.status, json.loads(body)), (200, {'in': 'command: make', 'out': 'built', 'done': True}))
        for bad in ('t:s1:../../etc:1:Bash', 't:nope:main:1:Bash', 'garbage', ''):
            self.assertEqual(self.request('GET', '/api/call?id=' + quote(bad))[0].status, 404, bad)

    def test_focus_without_herdr_is_not_found(self):
        self.assertEqual(self.post('/api/focus', {'pid': 1})[0].status, 404)

    def test_unknown_paths(self):
        self.assertEqual(self.request('GET', '/nope')[0].status, 404)
        self.assertEqual(self.post('/nope', {})[0].status, 404)


if __name__ == '__main__':
    unittest.main()
