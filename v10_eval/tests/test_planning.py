import tempfile
import unittest
from pathlib import Path

from agent import PLAN_ON, Agent, AgentConfig
from llm import Usage
from llm.fake import FakeLLM, call, reply
from planning import TodoList, read_only_registry
from safety import Guard
from tools import ToolError, default_tools
from ui import QuietUI


def make(responses, guard=None, **cfg):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "app.py").write_text("def handler():\n    return 'v1'\n")
    fake = FakeLLM(responses)
    agent = Agent(fake, default_tools(str(tmp), timeout=5), AgentConfig(cwd=str(tmp), repo_map_tokens=0, **cfg),
                  guard or Guard("full-auto"), ui=QuietUI())
    return agent, fake, tmp


class TestTodoList(unittest.TestCase):
    def test_write_and_render(self):
        t = TodoList()
        out = t.write([{"content": "read code", "status": "completed"},
                       {"content": "fix bug", "status": "in_progress"},
                       {"content": "run tests", "status": "pending"}])
        self.assertIn("(1/3 done)", out)
        self.assertIn("[x] read code", out)
        self.assertIn("[~] fix bug", out)
        self.assertEqual(len(t.unfinished()), 2)

    def test_validation(self):
        t = TodoList()
        with self.assertRaises(ToolError):
            t.write([{"content": "a", "status": "in_progress"}, {"content": "b", "status": "in_progress"}])
        with self.assertRaises(ToolError):
            t.write([{"content": "a", "status": "doing"}])
        with self.assertRaises(ToolError):
            t.write([{"content": " ", "status": "pending"}])


class TestTodosInAgent(unittest.TestCase):
    def test_nudge_once_when_stopping_with_unfinished(self):
        agent, fake, _ = make([
            reply("", call("todo_write", todos=[{"content": "step A", "status": "in_progress"},
                                                {"content": "step B", "status": "pending"}])),
            reply("I'm done"),          # 미완료가 남았는데 멈춤 → 상기
            reply("really done"),       # 두 번째는 그대로 종료
        ])
        result = agent.run_turn("do A and B")
        self.assertEqual(result["result"], "really done")
        self.assertIn("unfinished todos", fake.calls[2]["messages"][-1].content)
        self.assertIn("[~] step A", fake.calls[2]["messages"][-1].content)

    def test_no_nudge_when_all_completed(self):
        agent, fake, _ = make([
            reply("", call("todo_write", todos=[{"content": "A", "status": "completed"}])),
            reply("done"),
        ])
        self.assertEqual(agent.run_turn("x")["result"], "done")
        self.assertEqual(len(fake.calls), 2)


class TestSubagent(unittest.TestCase):
    def test_explore_subagent_reports_and_is_read_only(self):
        # 같은 FakeLLM을 부모/자식이 공유: 응답이 호출 순서대로 소비된다
        agent, fake, tmp = make([
            reply("", call("task", description="find handler", prompt="Where is handler defined?",
                           subagent_type="explore")),
            # --- 서브 에이전트 ---
            reply("", call("grep", pattern="def handler"),
                  call("str_replace_editor", command="str_replace", path="app.py", old_str="v1", new_str="HACKED"),
                  call("bash", command="touch created.txt")),
            reply("handler is defined in app.py:1"),
            # --- 부모 ---
            reply("The handler lives in app.py"),
        ])
        result = agent.run_turn("where is the handler?")
        self.assertEqual(result["result"], "The handler lives in app.py")
        sub_results = fake.calls[2]["messages"][-3:]
        self.assertIn("app.py:1:", sub_results[0].content)          # grep 허용
        self.assertIn("read-only", sub_results[1].content)          # 편집 거부
        self.assertTrue(sub_results[2].is_error)                     # MEDIUM 명령 거부
        self.assertEqual((tmp / "app.py").read_text(), "def handler():\n    return 'v1'\n")
        self.assertFalse((tmp / "created.txt").exists())
        # 부모는 서브 에이전트의 최종 보고만 받는다 (서브의 grep 결과는 부모 대화에 없음)
        parent_last = fake.calls[3]["messages"][-1]
        self.assertIn("handler is defined in app.py:1", parent_last.content)
        self.assertIn("sub-agent 'find handler' finished", parent_last.content)
        self.assertEqual(len(fake.calls[3]["messages"]), 3)
        # 서브 에이전트는 task 도구가 없고, system에 서브 에이전트 안내가 붙는다
        sub_tools = [t["name"] for t in fake.calls[1]["tools"]]
        self.assertNotIn("task", sub_tools)
        self.assertIn("<subagent>", fake.calls[1]["system"])

    def test_subagent_usage_counted(self):
        agent, _, _ = make([
            reply("", call("task", description="d", prompt="p")),
            reply("report", usage=Usage(100, 50)),
            reply("ok"),
        ])
        result = agent.run_turn("x")
        self.assertEqual(result["input_tokens"], 10 + 100 + 10)

    def test_read_only_registry(self):
        reg = read_only_registry(default_tools(tempfile.mkdtemp()))
        self.assertEqual(list(reg.tools), ["bash", "str_replace_editor", "grep", "glob", "repo_map"])
        out, err = reg.run("str_replace_editor", {"command": "create", "path": "x.py", "file_text": ""})
        self.assertTrue(err)


class TestPlanMode(unittest.TestCase):
    def test_plan_mode_blocks_edits_and_notifies(self):
        agent, fake, tmp = make([
            reply("", call("str_replace_editor", command="view", path="app.py"),
                  call("str_replace_editor", command="str_replace", path="app.py", old_str="v1", new_str="v2")),
            reply("PLAN: change v1 to v2 in app.py"),
            reply("", call("str_replace_editor", command="str_replace", path="app.py", old_str="v1", new_str="v2")),
            reply("implemented"),
        ])
        agent.set_plan_mode(True)
        agent.run_turn("change the version")
        self.assertTrue(fake.calls[0]["messages"][0].content.startswith(PLAN_ON))
        results = fake.calls[1]["messages"][-2:]
        self.assertFalse(results[0].is_error)                       # view는 허용
        self.assertIn("Read-only mode", results[1].content)          # 편집은 차단
        self.assertIn("v1", (tmp / "app.py").read_text())
        # 계획 승인 → 모드 해제 → 구현
        agent.set_plan_mode(False)
        self.assertEqual(agent.run_turn("go ahead")["result"], "implemented")
        self.assertIn("Plan mode is OFF", fake.calls[2]["messages"][-1].content)
        self.assertIn("v2", (tmp / "app.py").read_text())
        # 도구 목록은 계획 모드와 무관하게 동일 (프롬프트 캐시 유지)
        self.assertEqual(fake.calls[0]["tools"], fake.calls[2]["tools"])


if __name__ == "__main__":
    unittest.main()
