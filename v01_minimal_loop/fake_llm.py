"""테스트용 가짜 LLM: 미리 정해둔 응답을 순서대로 돌려준다.

mini-swe-agent의 DeterministicModel과 같은 아이디어. API 비용 없이 루프를 검증할 수 있다.
"""


class FakeLLM:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []  # 매 호출 때 받은 messages (검증용)

    def __call__(self, system: str, messages: list[dict]) -> str:
        self.calls.append([dict(m) for m in messages])
        if not self.responses:
            raise RuntimeError("FakeLLM: 준비된 응답이 더 없습니다")
        return self.responses.pop(0)


def bash(cmd: str, thought: str = "doing it") -> str:
    return f"THOUGHT: {thought}\n\n```bash\n{cmd}\n```"
