import tempfile
import unittest
from pathlib import Path

from agent import Agent, AgentConfig
from context import Condenser, LoopDetector, mask_observations, render_transcript, strip_provider_raw, truncate_middle
from llm import APIError, Message, ToolCall, Usage
from llm.anthropic import to_anthropic_messages
from llm.fake import FakeLLM, call, reply
from safety import Guard
from tools import default_tools
from ui import QuietUI


def convo(n_rounds: int, output: str = "x" * 400):
    msgs = [Message("user", "task")]
    for i in range(n_rounds):
        msgs.append(Message("assistant", f"step {i}", [ToolCall(f"c{i}", "bash", {"command": f"cmd{i}"})],
                            raw={"provider": "anthropic", "content": [{"type": "thinking", "signature": "S"}]}))
        msgs.append(Message("tool", output, tool_call_id=f"c{i}"))
    return msgs


class TestTruncate(unittest.TestCase):
    def test_short_untouched(self):
        self.assertEqual(truncate_middle("abc", 10), "abc")

    def test_head_and_tail_kept(self):
        text = "HEAD" + "m" * 1000 + "TAIL"
        out = truncate_middle(text, 100)
        self.assertTrue(out.startswith("HEAD"))
        self.assertTrue(out.endswith("TAIL"))
        self.assertIn("omitted", out)

    def test_saves_full_output(self):
        p = Path(tempfile.mkdtemp()) / "o" / "bash_1.txt"
        text = "\n".join(f"line {i}" for i in range(1000))
        out = truncate_middle(text, 200, p, "o/bash_1.txt")
        self.assertEqual(p.read_text(), text)
        self.assertIn("o/bash_1.txt", out)


class TestMasking(unittest.TestCase):
    def test_keeps_last_n(self):
        out = mask_observations(convo(8), keep_last=3)
        tools = [m for m in out if m.role == "tool"]
        self.assertTrue(all(m.content.startswith("[Old output of bash") for m in tools[:5]))
        self.assertTrue(all(m.content == "x" * 400 for m in tools[5:]))
        self.assertEqual(len(out), len(convo(8)))  # 메시지 수(=도구 쌍)는 그대로

    def test_strip_raw(self):
        out = strip_provider_raw(convo(2))
        self.assertTrue(all(m.raw is None for m in out))
        self.assertNotIn("thinking", str(to_anthropic_messages(out)))


class TestCondenser(unittest.TestCase):
    def test_masking_is_enough(self):
        c = Condenser(FakeLLM([]), trigger_tokens=1000, keep_observations=2, log=lambda *_: None)
        out = c.condense(convo(10))  # 10 * 400자 = ~1000 토큰 → 마스킹 후 작아짐
        self.assertEqual(len(out), 21)
        self.assertEqual(c.n_condensations, 1)

    def test_summary_keeps_task_and_tail_pairs(self):
        fake = FakeLLM([reply("SUMMARY: did steps 0-6")])
        c = Condenser(fake, trigger_tokens=100, keep_tail=3, log=lambda *_: None)
        msgs = convo(10, output="y" * 2000)
        out = c.condense(msgs)
        self.assertEqual(out[0].content, "task")
        self.assertIn("SUMMARY: did steps 0-6", out[1].content)
        self.assertEqual(out[2].role, "assistant")  # 꼬리는 assistant부터 → tool 결과가 짝 없이 남지 않음
        ids_called = {tc.id for m in out if m.role == "assistant" for tc in m.tool_calls}
        ids_answered = {m.tool_call_id for m in out if m.role == "tool"}
        self.assertEqual(ids_called, ids_answered)
        # 요약 요청에는 도구를 주지 않고, 대화를 텍스트로 렌더링해서 보낸다
        self.assertEqual(fake.calls[0]["tools"], [])
        self.assertIn("<TOOL_RESULT>", fake.calls[0]["messages"][0].content)
        # anthropic 변환이 유효한 교대 구조인지
        roles = [m["role"] for m in to_anthropic_messages(out)]
        self.assertTrue(all(a != b for a, b in zip(roles, roles[1:])))

    def test_render_transcript_truncates_tool_output(self):
        text = render_transcript([Message("tool", "z" * 5000, tool_call_id="a")], max_tool_chars=100)
        self.assertIn("[truncated]", text)
        self.assertLess(len(text), 300)


class TestLoopDetector(unittest.TestCase):
    def test_detects(self):
        d = LoopDetector(threshold=3)
        self.assertFalse(d.observe("bash", {"command": "ls"}))
        self.assertFalse(d.observe("bash", {"command": "ls"}))
        self.assertTrue(d.observe("bash", {"command": "ls"}))
        self.assertFalse(d.observe("bash", {"command": "pwd"}))


class TestAgentContext(unittest.TestCase):
    def make(self, responses, **cfg):
        tmp = tempfile.mkdtemp()
        fake = FakeLLM(responses)
        agent = Agent(fake, default_tools(tmp, timeout=5), AgentConfig(cwd=tmp, **cfg), Guard("full-auto"),
                      ui=QuietUI())
        return agent, fake, Path(tmp)

    def test_long_output_truncated_and_saved(self):
        agent, fake, tmp = self.make([reply("", call("bash", command="seq 1 20000")), reply("ok")],
                                     max_output_chars=1000)
        agent.run("x")
        obs = fake.calls[1]["messages"][-1].content
        self.assertLess(len(obs), 1500)
        self.assertIn(".agent/outputs/bash_1.txt", obs)
        self.assertIn("20000", (tmp / ".agent/outputs/bash_1.txt").read_text())

    def test_condenses_when_usage_exceeds_limit(self):
        big = Usage(input_tokens=5000, output_tokens=10)
        responses = [reply("", call("bash", command=f"echo {i}"), usage=big) for i in range(8)] + [reply("done")]
        agent, fake, _ = self.make(responses, context_limit=4000)
        result = agent.run("x")
        self.assertGreaterEqual(result["condensations"], 1)
        # 압축 이후의 호출에서는 오래된 결과가 가려져 있다
        later = fake.calls[-1]["messages"]
        self.assertTrue(any(m.content.startswith("[Old output") for m in later if m.role == "tool"))

    def test_overflow_error_forces_summary_and_retries(self):
        class Overflowing(FakeLLM):
            def complete(self, system, messages, tools):
                if tools and not getattr(self, "raised", False):
                    self.raised = True
                    raise APIError(400, '{"error": {"message": "prompt is too long: 250000 tokens > 200000"}}')
                return super().complete(system, messages, tools)

        tmp = tempfile.mkdtemp()
        fake = Overflowing([reply("SUMMARY"), reply("done")])
        agent = Agent(fake, default_tools(tmp), AgentConfig(cwd=tmp), Guard("full-auto"), ui=QuietUI())
        self.assertEqual(agent.run("x")["exit_status"], "Done")

    def test_loop_warning_injected(self):
        responses = [reply("", call("bash", command="ls")) for _ in range(4)] + [reply("ok")]
        agent, fake, _ = self.make(responses)
        agent.run("x")
        self.assertIn("stuck in a loop", fake.calls[4]["messages"][-1].content)


if __name__ == "__main__":
    unittest.main()
