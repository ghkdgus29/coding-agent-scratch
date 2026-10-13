"""평가 하네스 자체의 테스트 (API 불필요)."""
import argparse
import sys
import tempfile
import unittest
from pathlib import Path

EVAL = Path(__file__).resolve().parent.parent / "eval"
sys.path.insert(0, str(EVAL))

import harness

# 하네스가 subprocess로 실행할 "가짜 에이전트": fix_off_by_one 버그만 고친다
FAKE_AGENT = '''
import argparse, json, pathlib
ap = argparse.ArgumentParser()
ap.add_argument("task"); ap.add_argument("--cwd"); ap.add_argument("--model"); ap.add_argument("--yes", action="store_true")
ap.add_argument("--step-limit"); ap.add_argument("--stats-json")
a = ap.parse_args()
p = pathlib.Path(a.cwd) / "pagination.py"
if p.exists():
    s = p.read_text().replace("start = page * per_page", "start = (page - 1) * per_page")
    s = s.replace("return len(items) // per_page", "return (len(items) + per_page - 1) // per_page")
    p.write_text(s)
if a.stats_json:
    json.dump({"exit_status": "Done", "steps": 2, "cost_usd": 0.01}, open(a.stats_json, "w"))
'''


class TestTasks(unittest.TestCase):
    def test_all_tasks_valid_with_oracle(self):
        names = sorted(p.name for p in harness.TASKS_DIR.iterdir() if p.is_dir())
        self.assertGreaterEqual(len(names), 6)
        for name in names:
            r = harness.run_oracle(harness.Task.load(harness.TASKS_DIR / name))
            self.assertTrue(r.resolved, f"{name}: {r.error}")

    def test_unsolved_repo_fails_grading(self):
        task = harness.Task.load(harness.TASKS_DIR / "parse_duration")
        f2p, p2p = harness.grade(task, harness.prepare(task))
        self.assertFalse(f2p)
        self.assertTrue(p2p)

    def test_hidden_tests_removed_after_grading(self):
        task = harness.Task.load(harness.TASKS_DIR / "fix_off_by_one")
        workdir = harness.prepare(task)
        harness.grade(task, workdir)
        self.assertFalse((workdir / harness.HIDDEN).exists())


class TestRunAgent(unittest.TestCase):
    def test_fake_agent_end_to_end(self):
        agent_dir = Path(tempfile.mkdtemp())
        (agent_dir / "main.py").write_text(FAKE_AGENT)
        args = argparse.Namespace(model="fake", step_limit=5, effort=None, time_limit=60)
        results = {}
        for name in ("fix_off_by_one", "parse_duration"):
            task = harness.Task.load(harness.TASKS_DIR / name)
            results[name] = harness.run_agent(str(agent_dir), task, harness.prepare(task), args)
        self.assertTrue(results["fix_off_by_one"].resolved)
        self.assertEqual(results["fix_off_by_one"].stats["steps"], 2)
        self.assertFalse(results["parse_duration"].resolved)

    def test_agent_flags_differ_by_version(self):
        root = harness.PROJECT_ROOT
        if not (root / "v01_minimal_loop").exists():
            self.skipTest("version folders not found")
        self.assertNotIn("--stats-json", harness.agent_flags(root / "v01_minimal_loop"))
        self.assertIn("--stats-json", harness.agent_flags(root / "v10_eval"))


if __name__ == "__main__":
    unittest.main()
