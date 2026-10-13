import tempfile
import unittest
from pathlib import Path

from editor import Editor, find_similar_lines
from tools import ToolError, default_tools

SRC = '''def add(a, b):
    return a + b


def sub(a, b):
    return a - b
'''


class TestEditor(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "m.py").write_text(SRC)
        self.ed = Editor(str(self.root))

    def test_view_numbered_and_range(self):
        out = self.ed.view("m.py", [4, 6])
        self.assertIn("     5\tdef sub(a, b):", out)
        self.assertNotIn("def add", out)

    def test_view_bad_range(self):
        with self.assertRaises(ToolError):
            self.ed.view("m.py", [100, 200])

    def test_view_directory(self):
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "x.py").write_text("")
        out = self.ed.view(".")
        self.assertIn("pkg/", out)
        self.assertIn("pkg/x.py", out)

    def test_str_replace_unique(self):
        out = self.ed.str_replace("m.py", "return a - b", "return a - b  # minus")
        self.assertIn("# minus", (self.root / "m.py").read_text())
        self.assertIn("has been edited", out)
        self.assertIn("6\t    return a - b  # minus", out)  # 줄번호 붙은 스니펫

    def test_str_replace_not_found_gives_hint(self):
        with self.assertRaises(ToolError) as ctx:
            self.ed.str_replace("m.py", "def sub(a,b):\n  return a-b", "x")
        msg = str(ctx.exception)
        self.assertIn("did not appear verbatim", msg)
        self.assertIn("Did you mean", msg)
        self.assertIn("def sub(a, b):", msg)

    def test_str_replace_multiple_matches(self):
        with self.assertRaises(ToolError) as ctx:
            self.ed.str_replace("m.py", "(a, b):", "(a, b, c):")
        self.assertIn("appears 2 times", str(ctx.exception))
        self.assertIn("[1, 5]", str(ctx.exception))

    def test_already_applied_hint(self):
        with self.assertRaises(ToolError) as ctx:
            self.ed.str_replace("m.py", "return a * b", "return a + b")
        self.assertIn("already appears", str(ctx.exception))

    def test_syntax_error_rejected_and_file_unchanged(self):
        with self.assertRaises(ToolError) as ctx:
            self.ed.str_replace("m.py", "    return a + b", "    return (a + b")
        self.assertIn("syntax error", str(ctx.exception))
        self.assertEqual((self.root / "m.py").read_text(), SRC)

    def test_create_refuses_overwrite(self):
        with self.assertRaises(ToolError):
            self.ed.create("m.py", "x = 1\n")
        self.ed.create("sub/new.py", "x = 1\n")
        self.assertTrue((self.root / "sub" / "new.py").exists())

    def test_insert_and_undo(self):
        self.ed.insert("m.py", 0, "import math")
        self.assertTrue((self.root / "m.py").read_text().startswith("import math\n"))
        self.ed.undo_edit("m.py")
        self.assertEqual((self.root / "m.py").read_text(), SRC)
        with self.assertRaises(ToolError):
            self.ed.undo_edit("m.py")

    def test_crlf_file(self):
        (self.root / "w.txt").write_bytes(b"a\r\nb\r\nc\r\n")
        self.ed.str_replace("w.txt", "a\nb", "A\nB")
        self.assertEqual((self.root / "w.txt").read_bytes(), b"A\r\nB\r\nc\r\n")

    def test_dispatch_missing_params(self):
        with self.assertRaises(ToolError):
            self.ed.dispatch("str_replace", "m.py")
        with self.assertRaises(ToolError):
            self.ed.dispatch("fly", "m.py")


class TestSimilar(unittest.TestCase):
    def test_no_match_below_threshold(self):
        self.assertEqual(find_similar_lines("completely different", "abc\ndef"), "")


class TestRegistryIntegration(unittest.TestCase):
    def test_editor_errors_become_is_error(self):
        root = tempfile.mkdtemp()
        reg = default_tools(root)
        out, is_err = reg.run("str_replace_editor", {"command": "view", "path": "missing.py"})
        self.assertTrue(is_err)
        self.assertIn("does not exist", out)

    def test_confirm_only_for_mutations(self):
        root = tempfile.mkdtemp()
        asked = []
        reg = default_tools(root, confirm=lambda what: asked.append(what) or False)
        reg.run("str_replace_editor", {"command": "view", "path": "."})
        self.assertEqual(asked, [])
        out, is_err = reg.run("str_replace_editor", {"command": "create", "path": "a.py", "file_text": "x"})
        self.assertTrue(is_err)
        self.assertEqual(len(asked), 1)


if __name__ == "__main__":
    unittest.main()
