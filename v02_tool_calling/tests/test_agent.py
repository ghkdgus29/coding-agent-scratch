import tempfile
import unittest
from pathlib import Path

from agent import Agent, AgentConfig
from fake_llm import FakeLLM, response, text, tool_use
from tools import default_tools


def make(responses, step_limit=30, confirm=None):
    tmp = tempfile.mkdtemp()
    fake = FakeLLM(responses)
    agent = Agent(fake, default_tools(tmp, timeout=5, confirm=confirm), AgentConfig(step_limit=step_limit, cwd=tmp),
                  log=lambda *_: None)
    return agent, fake, Path(tmp)


class TestToolLoop(unittest.TestCase):
    def test_write_run_done(self):
        agent, fake, tmp = make([
            response(text("I'll create it"), tool_use("write_file", path="a.py", content="print(6*7)\n")),
            response(tool_use("bash", command="python3 a.py")),
            response(text("Done: printed 42")),
        ])
        result = agent.run("make a.py")
        self.assertEqual(result["exit_status"], "Done")
        self.assertEqual(result["result"], "Done: printed 42")
        self.assertEqual((tmp / "a.py").read_text(), "print(6*7)\n")
        # 3번째 호출 직전 마지막 메시지 = bash의 tool_result
        last = fake.calls[2]["messages"][-1]
        self.assertEqual(last["role"], "user")
        self.assertEqual(last["content"][0]["type"], "tool_result")
        self.assertIn("42", last["content"][0]["content"])

    def test_tool_specs_sent(self):
        agent, fake, _ = make([response(text("nothing to do"))])
        agent.run("x")
        names = [t["name"] for t in fake.calls[0]["tools"]]
        self.assertEqual(names, ["bash", "read_file", "write_file"])
        self.assertIn("input_schema", fake.calls[0]["tools"][0])

    def test_parallel_tool_calls_single_result_message(self):
        a, b = tool_use("bash", command="echo A"), tool_use("bash", command="echo B")
        agent, fake, _ = make([response(a, b), response(text("ok"))])
        agent.run("x")
        msg = fake.calls[1]["messages"][-1]
        self.assertEqual([r["tool_use_id"] for r in msg["content"]], [a["id"], b["id"]])

    def test_assistant_content_kept_verbatim(self):
        thinking = {"type": "thinking", "thinking": "", "signature": "sig123"}
        agent, fake, _ = make([response(thinking, tool_use("bash", command="true")), response(text("ok"))])
        agent.run("x")
        self.assertEqual(fake.calls[1]["messages"][1]["content"][0], thinking)

    def test_errors_become_is_error_results(self):
        agent, fake, _ = make([
            response(tool_use("read_file", path="nope.txt"), tool_use("no_such_tool"), tool_use("bash")),
            response(text("ok")),
        ])
        agent.run("x")
        results = fake.calls[1]["messages"][-1]["content"]
        self.assertTrue(all(r.get("is_error") for r in results))
        self.assertIn("File not found", results[0]["content"])
        self.assertIn("Unknown tool", results[1]["content"])
        self.assertIn("Invalid arguments", results[2]["content"])

    def test_max_tokens_truncated_tool_not_executed(self):
        agent, fake, tmp = make([
            response(tool_use("write_file", path="x.txt", content="partial"), stop_reason="max_tokens"),
            response(text("ok")),
        ])
        agent.run("x")
        self.assertFalse((tmp / "x.txt").exists())
        self.assertTrue(fake.calls[1]["messages"][-1]["content"][0]["is_error"])

    def test_refusal(self):
        agent, _, _ = make([response(stop_reason="refusal")])
        self.assertEqual(agent.run("x")["exit_status"], "Refused")

    def test_step_limit(self):
        agent, _, _ = make([response(tool_use("bash", command="true")) for _ in range(5)], step_limit=2)
        self.assertEqual(agent.run("x")["exit_status"], "LimitsExceeded")

    def test_user_rejects(self):
        agent, fake, _ = make([response(tool_use("bash", command="rm -rf x")), response(text("ok"))],
                              confirm=lambda what: False)
        agent.run("x")
        self.assertIn("rejected", fake.calls[1]["messages"][-1]["content"][0]["content"])


class TestAnthropicRequest(unittest.TestCase):
    def test_body_has_effort_and_fallbacks(self):
        import json
        import os
        from unittest import mock

        import llm
        from test_llm import fake_urlopen

        opener, cap = fake_urlopen(response(text("hi")))
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), mock.patch("urllib.request.urlopen", opener):
            llm.anthropic_messages("sys", [{"role": "user", "content": "x"}], [{"name": "t"}], model="claude-opus-5-5")
        self.assertEqual(cap["body"]["output_config"], {"effort": "high"})
        self.assertEqual(cap["body"]["fallbacks"], "default")
        self.assertEqual(cap["headers"]["anthropic-beta"], "server-side-fallback-2026-07-01")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), mock.patch("urllib.request.urlopen", opener):
            llm.anthropic_messages("sys", [], [], model="claude-haiku-4-5")
        self.assertNotIn("output_config", cap["body"])
        self.assertNotIn("fallbacks", cap["body"])
        json.dumps(cap["body"])


if __name__ == "__main__":
    unittest.main()
