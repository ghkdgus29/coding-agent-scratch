import subprocess
import sys
import tempfile
import unittest


def run(text, *args):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
    return subprocess.run([sys.executable, "wordcount.py", f.name, *args], capture_output=True, text=True).stdout


class TestTop(unittest.TestCase):
    def test_top_two(self):
        self.assertEqual(run("b a c a b a", "--top", "2"), "total: 6\na: 3\nb: 2\n")

    def test_ties_alphabetical(self):
        self.assertEqual(run("z y x", "--top", "2"), "total: 3\nx: 1\ny: 1\n")

    def test_without_flag_unchanged(self):
        self.assertEqual(run("a b"), "total: 2\n")
