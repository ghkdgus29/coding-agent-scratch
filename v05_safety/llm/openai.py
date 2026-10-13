"""OpenAI Chat Completions 호환 provider (OpenAI, OpenRouter, Ollama, vLLM 등)."""

import json
import os

from .base import LLM, LLMResponse, Message, ToolCall, Usage, post_json, retry_call

STOP_MAP = {"stop": "end_turn", "tool_calls": "tool_use", "function_call": "tool_use",
            "length": "max_tokens", "content_filter": "refusal"}
# 원본 assistant 메시지에서 다시 보내줘야 하는 필드 (OpenRouter의 reasoning 모델 등)
PASSTHROUGH_KEYS = ("reasoning", "reasoning_details")


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in tools]


def to_openai_messages(system: str, messages: list[Message]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "tool":
            # OpenAI에는 is_error 필드가 없다 → 내용에 표시
            content = ("ERROR: " + m.content) if m.is_error else m.content
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": content})
        elif m.role == "assistant":
            msg: dict = {"role": "assistant", "content": m.content or None}
            if m.tool_calls:
                msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                      "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                                     for tc in m.tool_calls]
            if m.raw and m.raw.get("provider") == "openai":
                for k in PASSTHROUGH_KEYS:
                    if k in m.raw["message"]:
                        msg[k] = m.raw["message"][k]
            out.append(msg)
    return out


def from_openai_response(resp: dict) -> LLMResponse:
    choice = resp["choices"][0]
    raw_msg = choice.get("message") or {}
    calls = []
    for tc in raw_msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        try:
            args, err = json.loads(fn.get("arguments") or "{}"), None
            if not isinstance(args, dict):
                args, err = {}, "arguments must be a JSON object"
        except json.JSONDecodeError as e:
            args, err = {}, f"invalid JSON arguments: {e}"
        calls.append(ToolCall(tc.get("id", ""), fn.get("name", ""), args, err))
    u = resp.get("usage") or {}
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    usage = Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0), cached, 0)
    msg = Message("assistant", raw_msg.get("content") or "", calls, raw={"provider": "openai", "message": raw_msg})
    stop = STOP_MAP.get(choice.get("finish_reason") or "", "other")
    if calls and stop == "end_turn":  # 일부 서버는 tool call이 있어도 "stop"을 준다
        stop = "tool_use"
    return LLMResponse(msg, stop, usage, resp.get("model", ""))


class OpenAILLM(LLM):
    name = "openai"

    def __init__(self, model: str, base_url: str = "https://api.openai.com/v1", api_key_env: str | None = "OPENAI_API_KEY",
                 max_tokens: int | None = None, extra_body: dict | None = None, log=print):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self.extra_body = extra_body or {}
        self.log = log

    def build_request(self, system: str, messages: list[Message], tools: list[dict]):
        headers = {}
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise SystemExit(f"{self.api_key_env} 가 설정되어 있지 않습니다.")
            headers["authorization"] = f"Bearer {key}"
        body: dict = {"model": self.model, "messages": to_openai_messages(system, messages)}
        if tools:
            body["tools"] = to_openai_tools(tools)
        if self.max_tokens:
            key = "max_completion_tokens" if "api.openai.com" in self.base_url else "max_tokens"
            body[key] = self.max_tokens
        body.update(self.extra_body)
        return headers, body

    def complete(self, system: str, messages: list[Message], tools: list[dict]) -> LLMResponse:
        headers, body = self.build_request(system, messages, tools)
        resp = retry_call(lambda: post_json(self.base_url + "/chat/completions", headers, body), log=self.log)
        return from_openai_response(resp)
