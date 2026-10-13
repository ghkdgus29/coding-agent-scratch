"""Anthropic Messages API provider."""

import os
from collections.abc import Callable

from .base import LLM, LLMResponse, Message, ToolCall, Usage, post_json, retry_call
from .stream import accumulate_anthropic, post_sse

API_URL = "https://api.anthropic.com/v1/messages"
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
STOP_MAP = {"end_turn": "end_turn", "stop_sequence": "end_turn", "tool_use": "tool_use",
            "max_tokens": "max_tokens", "refusal": "refusal"}


def supports_effort(model: str) -> bool:
    return model.startswith(("claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-opus-4-7", "claude-opus-4-8"))


def to_anthropic_messages(messages: list[Message]) -> list[dict]:
    """중립 메시지 → Anthropic messages.

    - assistant: 같은 provider가 만든 raw가 있으면 그대로 (thinking 블록 보존)
    - tool: 연속된 결과들을 user 메시지 하나의 tool_result 블록들로 합친다
    - 연속된 user 메시지는 하나로 합친다 (예: tool_result 뒤에 붙는 안내 문구)
    """
    out: list[dict] = []

    def push_user_blocks(blocks: list[dict]) -> None:
        if out and out[-1]["role"] == "user":
            prev = out[-1]["content"]
            if isinstance(prev, str):
                prev = [{"type": "text", "text": prev}]
            out[-1]["content"] = prev + blocks
        else:
            out.append({"role": "user", "content": blocks})

    for m in messages:
        if m.role == "user":
            if out and out[-1]["role"] == "user":
                push_user_blocks([{"type": "text", "text": m.content}])
            else:
                out.append({"role": "user", "content": m.content})
        elif m.role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
            if m.is_error:
                block["is_error"] = True
            push_user_blocks([block])
        elif m.role == "assistant":
            if m.raw and m.raw.get("provider") == "anthropic":
                content = m.raw["content"]
            else:  # 다른 provider가 만든 메시지 → 중립 필드로 재구성
                content = ([{"type": "text", "text": m.content}] if m.content else []) + [
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments} for tc in m.tool_calls
                ]
            out.append({"role": "assistant", "content": content})
    return out


def from_anthropic_response(resp: dict) -> LLMResponse:
    content = resp.get("content", [])
    text = "\n".join(b["text"] for b in content if b.get("type") == "text" and b.get("text"))
    calls = []
    for b in content:
        if b.get("type") == "tool_use":
            inp = b.get("input") or {}
            if "__invalid_json__" in inp:  # 스트리밍 중 잘린/깨진 JSON (stream.py 참고)
                calls.append(ToolCall(b["id"], b["name"], {}, "invalid JSON arguments (stream was cut off?)"))
            else:
                calls.append(ToolCall(b["id"], b["name"], inp))
    u = resp.get("usage") or {}
    usage = Usage(u.get("input_tokens", 0), u.get("output_tokens", 0),
                  u.get("cache_read_input_tokens", 0) or 0, u.get("cache_creation_input_tokens", 0) or 0)
    msg = Message("assistant", text, calls, raw={"provider": "anthropic", "content": content})
    return LLMResponse(msg, STOP_MAP.get(resp.get("stop_reason") or "", "other"), usage, resp.get("model", ""))


class AnthropicLLM(LLM):
    name = "anthropic"

    def __init__(self, model: str = "claude-opus-5-5", max_tokens: int = 16000, effort: str | None = "high",
                 api_key: str | None = None, base_url: str = API_URL, prompt_caching: bool = True, log=print):
        self.model = model
        self.max_tokens = max_tokens
        self.stream_max_tokens = 64000  # 스트리밍은 HTTP 타임아웃 걱정이 없으니 넉넉하게
        self.prompt_caching = prompt_caching
        self.effort = effort
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.url = base_url
        self.log = log

    def build_request(self, system: str, messages: list[Message], tools: list[dict], stream: bool = False):
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        body: dict = {"model": self.model, "max_tokens": self.stream_max_tokens if stream else self.max_tokens,
                      "system": system, "messages": to_anthropic_messages(messages)}
        if stream:
            body["stream"] = True
        if self.prompt_caching:
            # 자동 프롬프트 캐싱: 이전 요청과 같은 앞부분(tools → system → messages)을 캐시에서 싸게 읽는다.
            # 에이전트는 매 턴 거의 같은 대화를 다시 보내므로 효과가 매우 크다.
            body["cache_control"] = {"type": "ephemeral"}
        if tools:
            body["tools"] = tools  # 중립 tool spec이 Anthropic 포맷(name/description/input_schema)과 같다
        if self.effort and supports_effort(self.model):
            body["output_config"] = {"effort": self.effort}
        if self.model in FALLBACK_MODELS:
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
            body["fallbacks"] = "default"
        return headers, body

    def complete(self, system: str, messages: list[Message], tools: list[dict]) -> LLMResponse:
        if not self.api_key:
            raise SystemExit("ANTHROPIC_API_KEY 가 설정되어 있지 않습니다.")
        headers, body = self.build_request(system, messages, tools)
        resp = retry_call(lambda: post_json(self.url, headers, body), log=self.log)
        return from_anthropic_response(resp)

    def stream(self, system: str, messages: list[Message], tools: list[dict],
               on_text: Callable[[str], None] | None = None) -> LLMResponse:
        if not self.api_key:
            raise SystemExit("ANTHROPIC_API_KEY 가 설정되어 있지 않습니다.")
        headers, body = self.build_request(system, messages, tools, stream=True)
        resp = retry_call(lambda: accumulate_anthropic(post_sse(self.url, headers, body), on_text), log=self.log)
        return from_anthropic_response(resp)
