"""테스트용 가짜 LLM (Anthropic 응답 포맷을 흉내낸다)."""

import itertools

_ids = itertools.count(1)


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def tool_use(name: str, **inp) -> dict:
    return {"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": name, "input": inp}


def response(*blocks: dict, stop_reason: str = "") -> dict:
    if not stop_reason:
        stop_reason = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return {"content": list(blocks), "stop_reason": stop_reason, "usage": {"input_tokens": 0, "output_tokens": 0}}


class FakeLLM:
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        if not self.responses:
            raise RuntimeError("FakeLLM: 준비된 응답이 더 없습니다")
        return self.responses.pop(0)
