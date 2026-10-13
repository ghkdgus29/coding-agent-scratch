"""provider 변환 로직 테스트 (HTTP 없이)."""
import io
import json
import os
import unittest
import urllib.error
from unittest import mock

from llm.anthropic import AnthropicLLM, from_anthropic_response, to_anthropic_messages
from llm.base import APIError, Message, ToolCall, retry_call
from llm.openai import OpenAILLM, from_openai_response, to_openai_messages, to_openai_tools

TOOLS = [{"name": "bash", "description": "run", "input_schema": {"type": "object", "properties": {}}}]


def history():
    """user → assistant(tool_calls 2개) → tool 결과 2개 → user 안내 문구"""
    return [
        Message("user", "do it"),
        Message("assistant", "ok", [ToolCall("A", "bash", {"command": "ls"}), ToolCall("B", "bash", {"command": "pwd"})]),
        Message("tool", "files", tool_call_id="A"),
        Message("tool", "boom", tool_call_id="B", is_error=True),
        Message("user", "note: keep going"),
    ]


class TestAnthropicConversion(unittest.TestCase):
    def test_tool_results_merged_into_one_user_message(self):
        out = to_anthropic_messages(history())
        self.assertEqual([m["role"] for m in out], ["user", "assistant", "user"])
        blocks = out[2]["content"]
        self.assertEqual([b["type"] for b in blocks], ["tool_result", "tool_result", "text"])
        self.assertTrue(blocks[1]["is_error"])
        self.assertNotIn("is_error", blocks[0])
        self.assertEqual(out[1]["content"][1], {"type": "tool_use", "id": "A", "name": "bash", "input": {"command": "ls"}})

    def test_raw_content_replayed_verbatim(self):
        raw_content = [{"type": "thinking", "thinking": "", "signature": "S"}, {"type": "text", "text": "hi"}]
        resp = from_anthropic_response({"content": raw_content, "stop_reason": "end_turn",
                                        "usage": {"input_tokens": 3, "output_tokens": 1}})
        self.assertEqual(resp.message.content, "hi")
        out = to_anthropic_messages([Message("user", "x"), resp.message])
        self.assertEqual(out[1]["content"], raw_content)

    def test_parse_tool_use(self):
        resp = from_anthropic_response({
            "content": [{"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "ls"}}],
            "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 2, "cache_read_input_tokens": 4},
        })
        self.assertEqual(resp.stop_reason, "tool_use")
        self.assertEqual(resp.message.tool_calls[0].arguments, {"command": "ls"})
        self.assertEqual(resp.usage.cache_read_tokens, 4)

    def test_request_body(self):
        llm = AnthropicLLM("claude-opus-5-5", api_key="k")
        headers, body = llm.build_request("SYS", [Message("user", "x")], TOOLS)
        self.assertEqual(body["system"], "SYS")
        self.assertEqual(body["tools"], TOOLS)
        self.assertEqual(body["fallbacks"], "default")
        self.assertEqual(headers["x-api-key"], "k")


class TestOpenAIConversion(unittest.TestCase):
    def test_messages(self):
        out = to_openai_messages("SYS", history())
        self.assertEqual([m["role"] for m in out], ["system", "user", "assistant", "tool", "tool", "user"])
        tc = out[2]["tool_calls"][0]
        self.assertEqual(tc["function"]["name"], "bash")
        self.assertEqual(json.loads(tc["function"]["arguments"]), {"command": "ls"})  # 문자열로 직렬화
        self.assertTrue(out[4]["content"].startswith("ERROR: "))

    def test_tools(self):
        self.assertEqual(to_openai_tools(TOOLS)[0]["function"]["parameters"], TOOLS[0]["input_schema"])

    def test_parse_response_and_bad_json(self):
        resp = from_openai_response({"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "reasoning_details": [{"x": 1}],
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash", "arguments": "{\"command\": \"ls\"}"}},
                           {"id": "c2", "type": "function", "function": {"name": "bash", "arguments": "{oops"}}]}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3}})
        self.assertEqual(resp.stop_reason, "tool_use")
        self.assertEqual(resp.message.tool_calls[0].arguments, {"command": "ls"})
        self.assertIsNotNone(resp.message.tool_calls[1].parse_error)
        # reasoning_details는 다음 요청에 다시 실어 보낸다
        out = to_openai_messages("", [resp.message])
        self.assertEqual(out[0]["reasoning_details"], [{"x": 1}])

    @mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k"})
    def test_max_tokens_key(self):
        _, body = OpenAILLM("gpt-5", max_tokens=100).build_request("", [], [])
        self.assertIn("max_completion_tokens", body)
        _, body = OpenAILLM("m", base_url="http://localhost:11434/v1", api_key_env=None, max_tokens=100).build_request("", [], [])
        self.assertIn("max_tokens", body)


class TestCrossProvider(unittest.TestCase):
    def test_anthropic_history_to_openai(self):
        """Anthropic이 만든 assistant 메시지를 OpenAI로 보내도 깨지지 않아야 한다 (모델 전환)."""
        resp = from_anthropic_response({"content": [
            {"type": "thinking", "thinking": "", "signature": "S"},
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "ls"}}],
            "stop_reason": "tool_use", "usage": {}})
        out = to_openai_messages("", [Message("user", "x"), resp.message, Message("tool", "ok", tool_call_id="t1")])
        self.assertEqual(out[1]["tool_calls"][0]["id"], "t1")
        self.assertNotIn("thinking", json.dumps(out))


class TestRetry(unittest.TestCase):
    def test_retries_then_succeeds(self):
        attempts = []
        sleeps = []

        def fn():
            attempts.append(1)
            if len(attempts) < 3:
                raise APIError(529, "overloaded")
            return {"ok": True}

        self.assertEqual(retry_call(fn, sleep=sleeps.append, log=lambda *_: None), {"ok": True})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(sleeps), 2)

    def test_retry_after_header(self):
        sleeps = []
        calls = iter([APIError(429, "slow", {"retry-after": "7"}), None])

        def fn():
            e = next(calls)
            if e:
                raise e
            return {}

        retry_call(fn, sleep=sleeps.append, log=lambda *_: None)
        self.assertEqual(sleeps, [7.0])

    def test_non_retryable_raises_immediately(self):
        def fn():
            raise APIError(400, "bad request")

        with self.assertRaises(APIError):
            retry_call(fn, sleep=lambda s: self.fail("should not sleep"), log=lambda *_: None)

    def test_network_error_is_retryable(self):
        seq = iter([urllib.error.URLError("dns"), {}])

        def fn():
            v = next(seq)
            if isinstance(v, Exception):
                raise v
            return v

        self.assertEqual(retry_call(fn, sleep=lambda s: None, log=lambda *_: None), {})

    def test_gives_up(self):
        def fn():
            raise APIError(503, "down")

        with self.assertRaises(APIError):
            retry_call(fn, max_retries=2, sleep=lambda s: None, log=lambda *_: None)


class TestHTTPWiring(unittest.TestCase):
    def test_anthropic_end_to_end_with_fake_urlopen(self):
        payload = {"content": [{"type": "text", "text": "hey"}], "stop_reason": "end_turn",
                   "usage": {"input_tokens": 1, "output_tokens": 1}}

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", lambda req, timeout=None: R(json.dumps(payload).encode())):
            resp = AnthropicLLM(api_key="k").complete("s", [Message("user", "x")], [])
        self.assertEqual(resp.message.content, "hey")
        self.assertEqual(resp.stop_reason, "end_turn")


if __name__ == "__main__":
    unittest.main()
