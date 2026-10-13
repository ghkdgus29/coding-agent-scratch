import tempfile
import unittest
from pathlib import Path

from agent import Agent, AgentConfig
from llm.fake import FakeLLM, reply
from repomap import RepoMap, ident_weight, mentions, pagerank
from safety import Guard
from search import glob_files, grep, iter_files
from tools import ToolError, default_tools


def make_repo() -> Path:
    root = Path(tempfile.mkdtemp())
    files = {
        "shop/models.py": "class Product:\n    def price_with_tax(self, rate):\n        return 1\n\n\nclass _Internal:\n    pass\n",
        "shop/cart.py": "from shop.models import Product\n\n\ndef cart_total(items):\n"
                        "    return sum(p.price_with_tax(0.1) for p in items if isinstance(p, Product))\n",
        "shop/api.py": "from shop.cart import cart_total\nfrom shop.models import Product\n\n\n"
                       "def checkout(items):\n    return cart_total(items)\n\n\ndef list_products():\n    return [Product()]\n",
        "scripts/unused.py": "def lonely_helper_function():\n    pass\n",
        "README.md": "# shop\nTODO: add discounts\n",
        "node_modules/lib/index.js": "TODO: ignored\n",
        "build/out.py": "TODO = 1\n",
        "secret/data.txt": "TODO: gitignored\n",
        ".gitignore": "secret/\n*.log\n",
        "debug.log": "TODO in log\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.root = make_repo()

    def test_iter_files_skips_noise_and_gitignore(self):
        rels = {str(f.relative_to(self.root.resolve())) for f in iter_files(self.root)}
        self.assertIn("shop/models.py", rels)
        self.assertIn("README.md", rels)
        for ignored in ("node_modules/lib/index.js", "build/out.py", "secret/data.txt", "debug.log"):
            self.assertNotIn(ignored, rels)

    def test_grep(self):
        out = grep(self.root, "TODO")
        self.assertIn("README.md:2: TODO: add discounts", out)
        self.assertNotIn("node_modules", out)
        out = grep(self.root, r"def \w+", include="*.py")
        self.assertIn("shop/cart.py:4:", out)
        self.assertNotIn("README", out)

    def test_grep_limits_and_errors(self):
        self.assertIn("stopped after 2", grep(self.root, "def", max_results=2))
        with self.assertRaises(ToolError):
            grep(self.root, "(unclosed")
        self.assertIn("No matches", grep(self.root, "zzzz_nothing"))

    def test_glob(self):
        out = glob_files(self.root, "shop/*.py")
        self.assertEqual(set(out.splitlines()), {"shop/models.py", "shop/cart.py", "shop/api.py"})
        self.assertIn("No files", glob_files(self.root, "*.rs"))


class TestPageRank(unittest.TestCase):
    def test_hub_ranks_highest(self):
        edges = {("a", "hub"): 1.0, ("b", "hub"): 1.0, ("c", "hub"): 1.0, ("hub", "a"): 1.0}
        r = pagerank(["a", "b", "c", "hub"], edges)
        self.assertEqual(max(r, key=r.get), "hub")
        self.assertAlmostEqual(sum(r.values()), 1.0, places=6)

    def test_personalization_shifts_rank(self):
        edges = {("a", "b"): 1.0, ("c", "d"): 1.0}
        r = pagerank("abcd", edges, personalization={"c": 1.0})
        self.assertGreater(r["d"], r["b"])

    def test_ident_weight(self):
        self.assertGreater(ident_weight("price_with_tax", 1, set()), ident_weight("run", 1, set()))
        self.assertLess(ident_weight("_run", 1, set()), ident_weight("run", 1, set()))
        self.assertGreater(ident_weight("run", 1, {"run"}), ident_weight("run", 1, set()))


class TestRepoMap(unittest.TestCase):
    def setUp(self):
        self.root = make_repo()
        self.rm = RepoMap(str(self.root))

    def test_map_contains_signatures_ranked(self):
        text = self.rm.get_map(2000)
        self.assertIn("shop/models.py:", text)
        self.assertIn("│class Product:", text)
        self.assertIn("│    def price_with_tax(self, rate):", text)
        # 많이 참조되는 models.py가 아무도 안 쓰는 scripts/unused.py보다 먼저
        self.assertLess(text.index("shop/models.py"), text.index("scripts/unused.py"))

    def test_budget_respected(self):
        small = self.rm.get_map(30)
        self.assertLessEqual(len(small) // 4, 30)
        self.assertGreater(len(self.rm.get_map(2000)), len(small))

    def test_mentions_bias(self):
        text = self.rm.get_map(40, mentioned_idents={"lonely_helper_function"})
        self.assertIn("lonely_helper_function", text)

    def test_mentions_parsing(self):
        files, idents = mentions("Fix cart_total in shop/cart.py please", self.root)
        self.assertEqual(files, {"shop/cart.py"})
        self.assertIn("cart_total", idents)

    def test_broken_python_file_ignored(self):
        (self.root / "shop" / "broken.py").write_text("def oops(:\n")
        self.assertIn("shop/models.py", self.rm.get_map(2000))


class TestAgentRepoMap(unittest.TestCase):
    def test_first_message_has_repo_map_and_task(self):
        root = make_repo()
        fake = FakeLLM([reply("done")])
        Agent(fake, default_tools(str(root)), AgentConfig(cwd=str(root)), Guard("full-auto"),
              log=lambda *_: None).run("make checkout apply discounts")
        first = fake.calls[0]["messages"][0].content
        self.assertIn("<repo_map>", first)
        self.assertIn("def checkout(items):", first)
        self.assertIn("<task>\nmake checkout apply discounts\n</task>", first)
        names = [t["name"] for t in fake.calls[0]["tools"]]
        self.assertEqual(names, ["bash", "str_replace_editor", "grep", "glob", "repo_map"])

    def test_search_tools_need_no_approval(self):
        root = make_repo()
        reg = default_tools(str(root))
        guard = Guard("suggest", ask=None)
        for name, args in [("grep", {"pattern": "x"}), ("glob", {"pattern": "*"}), ("repo_map", {})]:
            rv = reg.get(name).describe(args)
            self.assertTrue(guard.review(rv.kind, rv.key, rv.summary, rv.risk).allowed, name)
        out, err = reg.run("repo_map", {"focus_files": ["shop/api.py"], "max_tokens": 500})
        self.assertFalse(err)
        self.assertIn("shop/", out)


if __name__ == "__main__":
    unittest.main()
