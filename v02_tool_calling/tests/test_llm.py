"""실제 API 없이 urlopen을 가짜로 바꿔서 요청 포맷을 검증한다."""
import io
import json
import os
import unittest
from unittest import mock

import llm


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(payload):
    captured = {}

    def _open(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode())
        return FakeResponse(json.dumps(payload).encode())

    return _open, captured


class TestAnthropic(unittest.TestCase):
    @mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"})
    def test_request_and_parse(self):
        payload = {
            "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "hi"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }
        opener, cap = fake_urlopen(payload)
        with mock.patch("urllib.request.urlopen", opener):
            text, resp = llm.anthropic_chat([{"role": "user", "content": "hello"}], system="sys")
        self.assertEqual(text, "hi")  # thinking 블록은 무시
        self.assertEqual(cap["url"], llm.ANTHROPIC_URL)
        self.assertEqual(cap["headers"]["x-api-key"], "sk-test")
        self.assertEqual(cap["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(cap["body"]["system"], "sys")  # system은 별도 필드
        self.assertEqual(llm.usage_of("anthropic", resp), (10, 2))


class TestOpenAI(unittest.TestCase):
    @mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-oa"})
    def test_request_and_parse(self):
        payload = {
            "choices": [{"message": {"role": "assistant", "content": "yo"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
        }
        opener, cap = fake_urlopen(payload)
        with mock.patch("urllib.request.urlopen", opener):
            text, resp = llm.openai_chat([{"role": "user", "content": "hi"}], system="sys", max_tokens=100)
        self.assertEqual(text, "yo")
        self.assertTrue(cap["url"].endswith("/chat/completions"))
        self.assertEqual(cap["headers"]["authorization"], "Bearer sk-oa")
        # system은 messages의 첫 원소
        self.assertEqual(cap["body"]["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(cap["body"]["max_completion_tokens"], 100)
        self.assertEqual(llm.usage_of("openai", resp), (5, 1))


class TestErrors(unittest.TestCase):
    @mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"})
    def test_http_error(self):
        import urllib.error

        def boom(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 429, "rate", {}, io.BytesIO(b'{"error":"slow down"}'))

        with mock.patch("urllib.request.urlopen", boom):
            with self.assertRaises(llm.APIError) as ctx:
                llm.anthropic_chat([{"role": "user", "content": "x"}])
        self.assertEqual(ctx.exception.status, 429)


if __name__ == "__main__":
    unittest.main()
