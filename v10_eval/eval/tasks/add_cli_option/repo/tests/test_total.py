import subprocess
import sys
import tempfile
import unittest


class TestTotal(unittest.TestCase):
    def test_total_only(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("a b a")
        out = subprocess.run([sys.executable, "wordcount.py", f.name], capture_output=True, text=True).stdout
        self.assertEqual(out, "total: 3\n")
