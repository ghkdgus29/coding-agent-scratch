import json
import tempfile
import unittest
from pathlib import Path

from agent import INTERRUPTED_RESULT, Agent, AgentConfig
from instructions import load_instructions, render_instructions
from llm import APIError, Message, ToolCall, Usage
from llm.anthropic import from_anthropic_response
from llm.fake import FakeLLM, call, reply
from llm.openai import from_openai_response
from llm.stream import accumulate_anthropic, accumulate_openai, iter_sse
from pricing import cost_of, parse_price, price_for
from safety import Guard
from session import Session, list_sessions, open_session, repair_tool_pairs
from tools import Tool, ToolRegistry, _schema, default_tools
from ui import QuietUI


def sse(*events):
    """(event, data dict) → SSE 바이트 줄들"""
    lines = []
    for ev, data in events:
        if ev:
            lines.append(f"event: {ev}\n".encode())
        lines.append(f"data: {data if isinstance(data, str) else json.dumps(data)}\n".encode())
        lines.append(b"\n")
    return iter(lines)


class TestSSE(unittest.TestCase):
    def test_iter_sse_handles_comments_and_multiline(self):
        lines = iter([b": ping\n", b"event: a\n", b"data: 1\n", b"data: 2\n", b"\n", b"data: x\n"])
        self.assertEqual(list(iter_sse(lines)), [("a", "1\n2"), ("", "x")])

    def test_anthropic_accumulate(self):
        got = []
        events = iter_sse(sse(
            ("message_start", {"type": "message_start", "message": {"id": "m1", "model": "claude-opus-5-5",
                                                                    "usage": {"input_tokens": 50, "cache_read_input_tokens": 1000}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "SIG"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hel"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "lo"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 1}),
            ("content_block_start", {"type": "content_block_start", "index": 2,
                                     "content_block": {"type": "tool_use", "id": "t1", "name": "bash", "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"comm'}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": 'and": "ls"}'}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 2}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 30}}),
            ("message_stop", {"type": "message_stop"}),
        ))
        raw = accumulate_anthropic(events, got.append)
        self.assertEqual(got, ["Hel", "lo"])
        self.assertEqual(raw["content"][0], {"type": "thinking", "thinking": "", "signature": "SIG"})
        resp = from_anthropic_response(raw)
        self.assertEqual(resp.message.content, "Hello")
        self.assertEqual(resp.message.tool_calls[0].arguments, {"command": "ls"})
        self.assertEqual(resp.stop_reason, "tool_use")
        self.assertEqual((resp.usage.input_tokens, resp.usage.output_tokens, resp.usage.cache_read_tokens), (50, 30, 1000))

    def test_anthropic_truncated_json_marks_parse_error(self):
        events = iter_sse(sse(
            ("", {"type": "message_start", "message": {"usage": {}}}),
            ("", {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t", "name": "bash", "input": {}}}),
            ("", {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"com'}}),
            ("", {"type": "content_block_stop", "index": 0}),
            ("", {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 1}}),
        ))
        resp = from_anthropic_response(accumulate_anthropic(events))
        self.assertIsNotNone(resp.message.tool_calls[0].parse_error)

    def test_anthropic_error_event(self):
        events = iter_sse(sse(("error", {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})))
        with self.assertRaises(APIError) as ctx:
            accumulate_anthropic(events)
        self.assertEqual(ctx.exception.status, 529)  # → retry_call이 재시도한다

    def test_openai_accumulate(self):
        got = []
        chunks = [
            {"model": "m", "choices": [{"delta": {"role": "assistant", "content": "Hi "}}]},
            {"choices": [{"delta": {"content": "there"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "bash", "arguments": ""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"command\""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ": \"ls\"}"}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 7,
                                      "prompt_tokens_details": {"cached_tokens": 60}}},
        ]
        events = iter_sse(sse(*[("", c) for c in chunks], ("", "[DONE]")))
        resp = from_openai_response(accumulate_openai(events, got.append))
        self.assertEqual("".join(got), "Hi there")
        self.assertEqual(resp.message.tool_calls[0].arguments, {"command": "ls"})
        self.assertEqual(resp.stop_reason, "tool_use")
        self.assertEqual((resp.usage.input_tokens, resp.usage.cache_read_tokens, resp.usage.prompt_tokens), (40, 60, 100))


class TestPricing(unittest.TestCase):
    def test_cost(self):
        u = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=2_000_000)
        self.assertAlmostEqual(cost_of(u, "claude-opus-5-5"), 4.0 + 2.0 + 0.4)
        self.assertIsNone(cost_of(u, "some-unknown-model"))
        self.assertIsNotNone(price_for("anthropic/claude-sonnet-5-5"))  # OpenRouter 슬러그
        self.assertEqual(parse_price("2,10"), (2.0, 10.0, 0.2, 2.5))


class TestSession(unittest.TestCase):
    def test_roundtrip_replace_and_crash(self):
        cwd = tempfile.mkdtemp()
        s = Session.create(cwd, "fake")
        s.add_message(Message("user", "a"))
        s.add_message(Message("assistant", "b", [ToolCall("1", "bash", {"command": "ls"})], raw={"provider": "x"}))
        s.add_usage(Usage(10, 2), "fake")
        s.replace([Message("user", "summary")])
        s.add_message(Message("user", "c"))
        with open(s.path, "a") as f:
            f.write('{"type": "message", "mess')  # 크래시로 쓰다 만 줄
        messages, usage, meta = open_session(cwd, "last").load()
        self.assertEqual([m.content for m in messages], ["summary", "c"])
        self.assertEqual(usage.input_tokens, 10)
        self.assertEqual(meta["model"], "fake")
        self.assertEqual(open_session(cwd, s.id[:10]).id, s.id)
        self.assertEqual(len(list_sessions(cwd)), 1)

    def test_repair_tool_pairs(self):
        msgs = [Message("user", "x"),
                Message("assistant", "", [ToolCall("a", "bash", {}), ToolCall("b", "bash", {})]),
                Message("tool", "ok", tool_call_id="a")]
        fixed = repair_tool_pairs(msgs)
        answered = {m.tool_call_id for m in fixed if m.role == "tool"}
        self.assertEqual(answered, {"a", "b"})


class TestInstructions(unittest.TestCase):
    def test_root_to_cwd_order(self):
        root = Path(tempfile.mkdtemp())
        (root / ".git").mkdir()
        (root / "AGENTS.md").write_text("root rules")
        sub = root / "pkg"
        sub.mkdir()
        (sub / "CLAUDE.md").write_text("pkg rules")
        docs = load_instructions(str(sub))
        self.assertEqual([t for _, t in docs], ["root rules", "pkg rules"])
        self.assertIn("<project_instructions>", render_instructions(str(sub)))
        self.assertEqual(render_instructions(tempfile.mkdtemp()), "")


class TestAgentUX(unittest.TestCase):
    def make(self, responses, tools=None, **cfg):
        tmp = tempfile.mkdtemp()
        fake = FakeLLM(responses)
        agent = Agent(fake, tools or default_tools(tmp, timeout=5), AgentConfig(cwd=tmp, **cfg), Guard("full-auto"),
                      session=Session.create(tmp, "fake"), ui=QuietUI())
        return agent, fake, tmp

    def test_multi_turn_keeps_history(self):
        agent, fake, _ = self.make([reply("first"), reply("second")])
        agent.run_turn("hello")
        agent.run_turn("again")
        self.assertEqual([m.content for m in fake.calls[1]["messages"]], ["hello", "first", "again"])

    def test_ctrl_c_during_tools_fills_results_and_session(self):
        def boom(**kw):
            raise KeyboardInterrupt

        tempfile.mkdtemp()
        tools = ToolRegistry([Tool("slow", "x", _schema({}, []), boom)])
        agent, fake, _ = self.make([reply("", call("slow"), call("slow")), reply("recovered")], tools=tools)
        result = agent.run_turn("go")
        self.assertEqual(result["exit_status"], "Interrupted")
        tool_msgs = [m for m in agent.messages if m.role == "tool"]
        self.assertEqual(len(tool_msgs), 2)
        self.assertTrue(all(m.content == INTERRUPTED_RESULT for m in tool_msgs))
        # 다음 턴은 정상 진행되고, 세션 파일에서 복원해도 같은 상태
        self.assertEqual(agent.run_turn("continue")["result"], "recovered")
        restored, _, _ = Session(agent.session.path, agent.session.id).load()
        self.assertEqual(len(restored), len(agent.messages))

    def test_ctrl_c_during_llm_call_discards_partial(self):
        class Interrupting(FakeLLM):
            def complete(self, system, messages, tools):
                raise KeyboardInterrupt

        tmp = tempfile.mkdtemp()
        agent = Agent(Interrupting([]), default_tools(tmp), AgentConfig(cwd=tmp), Guard("full-auto"), ui=QuietUI())
        self.assertEqual(agent.run_turn("x")["exit_status"], "Interrupted")
        self.assertEqual([m.role for m in agent.messages], ["user"])

    def test_cost_limit(self):
        expensive = Usage(input_tokens=1_000_000, output_tokens=0)
        fake_responses = [reply("", call("bash", command="true"), usage=expensive) for _ in range(5)]
        agent, _, _ = self.make(fake_responses, cost_limit=5.0)
        agent.llm.model = "claude-opus-5-5"  # $4 / 1M input
        result = agent.run_turn("x")
        self.assertEqual(result["exit_status"], "CostLimitExceeded")
        self.assertEqual(result["steps"], 2)

    def test_streaming_text_goes_to_ui(self):
        seen = []

        class RecUI(QuietUI):
            def text(self, chunk):
                seen.append(chunk)

        tmp = tempfile.mkdtemp()
        agent = Agent(FakeLLM([reply("streamed!")]), default_tools(tmp), AgentConfig(cwd=tmp), Guard("full-auto"),
                      ui=RecUI())
        agent.run_turn("x")
        self.assertEqual(seen, ["streamed!"])


if __name__ == "__main__":
    unittest.main()
