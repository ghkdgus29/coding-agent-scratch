import tempfile
import unittest
from pathlib import Path

from agent import Agent, AgentConfig
from llm import Message, ToolCall
from llm.fake import FakeLLM, call, reply
from safety import Guard
from tools import default_tools
from ui import QuietUI


def make(responses, step_limit=30, guard=None):
    tmp = tempfile.mkdtemp()
    fake = FakeLLM(responses)
    agent = Agent(fake, default_tools(tmp, timeout=5), AgentConfig(step_limit=step_limit, cwd=tmp),
                  guard or Guard(mode="full-auto"), ui=QuietUI())
    return agent, fake, Path(tmp)


class TestAgent(unittest.TestCase):
    def test_write_run_done(self):
        agent, fake, tmp = make([
            reply("I'll create it", call("str_replace_editor", command="create", path="a.py", file_text="print(6*7)\n")),
            reply("", call("bash", command="python3 a.py")),
            reply("Done: printed 42"),
        ])
        result = agent.run("make a.py")
        self.assertEqual(result["exit_status"], "Done")
        self.assertEqual(result["input_tokens"], 30)  # FakeLLM은 호출당 10 토큰
        last = fake.calls[2]["messages"][-1]
        self.assertEqual(last.role, "tool")
        self.assertIn("42", last.content)

    def test_parallel_calls_each_get_result(self):
        a, b = call("bash", command="echo A"), call("bash", command="echo B")
        agent, fake, _ = make([reply("", a, b), reply("ok")])
        agent.run("x")
        msgs = fake.calls[1]["messages"]
        self.assertEqual([m.tool_call_id for m in msgs[-2:]], [a.id, b.id])

    def test_parse_error_reported(self):
        bad = ToolCall("c1", "bash", {}, parse_error="invalid JSON arguments")
        agent, fake, _ = make([reply("", bad), reply("ok")])
        agent.run("x")
        last = fake.calls[1]["messages"][-1]
        self.assertTrue(last.is_error)
        self.assertIn("Could not parse", last.content)

    def test_max_tokens_continue(self):
        agent, fake, _ = make([reply("partial...", stop="max_tokens"), reply("full answer")])
        result = agent.run("x")
        self.assertEqual(result["result"], "full answer")
        self.assertIn("cut off", fake.calls[1]["messages"][-1].content)

    def test_refusal(self):
        agent, _, _ = make([reply(stop="refusal")])
        self.assertEqual(agent.run("x")["exit_status"], "Refused")

    def test_step_limit(self):
        agent, _, _ = make([reply("", call("bash", command="true")) for _ in range(5)], step_limit=2)
        self.assertEqual(agent.run("x")["exit_status"], "LimitsExceeded")


class TestGuardInAgent(unittest.TestCase):
    def test_denied_with_feedback_not_executed(self):
        guard = Guard(mode="suggest", ask=lambda q: "use pytest instead")
        agent, fake, tmp = make([reply("", call("bash", command="touch x.txt")), reply("ok")], guard=guard)
        agent.run("x")
        last = fake.calls[1]["messages"][-1]
        self.assertTrue(last.is_error)
        self.assertIn("use pytest instead", last.content)
        self.assertFalse((tmp / "x.txt").exists())

    def test_noninteractive_blocks_high_risk(self):
        agent, fake, _ = make([reply("", call("bash", command="rm -rf /")), reply("ok")],
                              guard=Guard(mode="full-auto", ask=None))
        agent.run("x")
        self.assertIn("Blocked", fake.calls[1]["messages"][-1].content)

    def test_readonly_runs_without_asking(self):
        guard = Guard(mode="suggest", ask=lambda q: self.fail("should not ask"))
        agent, fake, _ = make([reply("", call("bash", command="ls -la"),
                                     call("str_replace_editor", command="view", path=".")), reply("ok")], guard=guard)
        agent.run("x")
        self.assertFalse(any(m.is_error for m in fake.calls[1]["messages"][-2:]))

    def test_wall_time_limit(self):
        tmp = tempfile.mkdtemp()
        agent = Agent(FakeLLM([reply("", call("bash", command="sleep 1.2")), reply("ok")]), default_tools(tmp),
                      AgentConfig(wall_time_limit=1, cwd=tmp), Guard(mode="full-auto"), ui=QuietUI())
        self.assertEqual(agent.run("x")["exit_status"], "TimeExceeded")


class TestMessageSerialization(unittest.TestCase):
    def test_roundtrip(self):
        m = Message("assistant", "hi", [ToolCall("1", "bash", {"command": "ls"})], raw={"provider": "x"})
        self.assertEqual(Message.from_dict(m.to_dict()), m)


if __name__ == "__main__":
    unittest.main()
