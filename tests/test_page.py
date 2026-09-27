"""The web page: its script parses, and everything it puts into HTML goes through esc()."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import ROOT

PAGE = (ROOT / 'server' / 'index.html').read_text()
SCRIPT = re.search(r'<script>(.*)</script>', PAGE, re.S).group(1)


class PageTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'node not installed')
    def test_script_parses(self):
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / 'page.js'
            js.write_text(SCRIPT)
            r = subprocess.run(['node', '--check', str(js)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_claude_code_branding_as_product_name(self):
        self.assertIn('<title>hivemap</title>', PAGE)
        self.assertNotIn('Claude Code · Map', PAGE)

    def test_data_attributes_are_escaped(self):
        # every ${...} inside a data-* attribute must be wrapped in esc(): ids come from files on disk
        for m in re.finditer(r'data-(?:sel|jump)="\$\{([^}]*)\}', SCRIPT):
            self.assertTrue(m.group(1).startswith('esc('), m.group(0))


if __name__ == '__main__':
    unittest.main()
