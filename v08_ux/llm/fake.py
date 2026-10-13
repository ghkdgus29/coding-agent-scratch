"""테스트용 가짜 LLM (provider 중립 응답)."""

import itertools

from .base import LLM, LLMResponse, Message, ToolCall, Usage

_ids = itertools.count(1)


def call(name: str, **arguments) -> ToolCall:
    return ToolCall(f"call_{next(_ids)}", name, arguments)


def reply(text: str = "", *calls: ToolCall, stop: str = "", usage: Usage = None) -> LLMResponse:
    stop = stop or ("tool_use" if calls else "end_turn")
    return LLMResponse(Message("assistant", text, list(calls)), stop, usage or Usage(10, 5))


class FakeLLM(LLM):
    name = "fake"
    model = "fake"

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, system: str, messages: list[Message], tools: list[dict]) -> LLMResponse:
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        if not self.responses:
            raise RuntimeError("FakeLLM: 준비된 응답이 더 없습니다")
        return self.responses.pop(0)
