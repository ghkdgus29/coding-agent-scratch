"""v02: native tool calling 에이전트 (Anthropic Messages API 포맷).

v01과의 차이
- 행동을 ```bash 정규식으로 파싱하지 않는다. API가 구조화된 tool_use 블록을 돌려준다.
- 종료 신호가 특수 문자열이 아니라 "도구를 더 이상 호출하지 않음"(stop_reason != tool_use)이다.
- 한 응답에 도구 호출이 여러 개 올 수 있다(병렬 호출). 결과는 한 user 메시지에 모아서 보낸다.

대화(messages) 모양
    user:      "작업: ..."
    assistant: [thinking?, text?, tool_use(id=A), tool_use(id=B)]   ← 응답 content 그대로
    user:      [tool_result(A), tool_result(B)]                     ← 한 메시지에 모두
    assistant: [text "다 했어요"]                                    ← tool_use 없음 → 끝
"""

import os
import platform
from collections.abc import Callable
from dataclasses import dataclass

from tools import ToolRegistry

SYSTEM_PROMPT = """You are a coding agent working in a local project directory.
Use the provided tools to inspect, run and modify code. Work step by step:
explore the relevant files, make changes, then verify them by running code or tests.
When the task is complete, reply with a short summary of what you did and do not call any more tools.

<system_information>
{system} {release} {machine}  (project root: {cwd})
</system_information>"""

# LLM 호출 함수 시그니처: (system, messages, tool_specs) -> Anthropic 응답 dict
LLMFn = Callable[[str, list[dict], list[dict]], dict]


@dataclass
class AgentConfig:
    step_limit: int = 30
    cwd: str = "."


class Agent:
    def __init__(self, llm: LLMFn, tools: ToolRegistry, config: AgentConfig, log: Callable[[str], None] = print):
        self.llm = llm
        self.tools = tools
        self.config = config
        self.log = log
        self.messages: list[dict] = []
        self.n_steps = 0
        u = platform.uname()
        self.system = SYSTEM_PROMPT.format(system=u.system, release=u.release, machine=u.machine,
                                           cwd=os.path.abspath(config.cwd))

    def run(self, task: str) -> dict:
        self.messages = [{"role": "user", "content": task}]
        while True:
            if 0 < self.config.step_limit <= self.n_steps:
                return self._exit("LimitsExceeded", f"step limit {self.config.step_limit} reached")
            self.n_steps += 1
            resp = self.llm(self.system, self.messages, self.tools.specs())

            # 응답 content를 "수정 없이" 그대로 저장한다.
            # thinking 블록에는 서명(signature)이 있어서, 다음 요청에 원본 그대로 보내야 한다.
            content = resp.get("content", [])
            self.messages.append({"role": "assistant", "content": content})
            stop = resp.get("stop_reason")
            for b in content:
                if b.get("type") == "text" and b.get("text"):
                    self.log(f"\n[step {self.n_steps}] assistant: {b['text']}")

            if stop == "refusal":
                return self._exit("Refused", "The model declined this request.")

            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if not tool_uses:
                if stop == "max_tokens":
                    self.messages.append({"role": "user", "content": "Your response was cut off. Please continue."})
                    continue
                return self._exit("Done", _text_of(content))

            self.messages.append({"role": "user", "content": [self._run_tool(b, stop) for b in tool_uses]})

    def _run_tool(self, block: dict, stop_reason: str) -> dict:
        name, args = block["name"], block.get("input") or {}
        if stop_reason == "max_tokens":
            # 출력 한도에 걸려 잘린 응답의 도구 입력은 불완전할 수 있다 → 실행하지 않는다.
            out, is_error = "Your response hit max_tokens, so this tool call was not executed. Retry with less output.", True
        else:
            self.log(f"[tool] {name}({_short(args)})")
            out, is_error = self.tools.run(name, args)
            self.log(("[error] " if is_error else "[result] ") + out[-1500:])
        result = {"type": "tool_result", "tool_use_id": block["id"], "content": out}
        if is_error:
            result["is_error"] = True
        return result

    def _exit(self, status: str, result: str) -> dict:
        return {"exit_status": status, "result": result, "steps": self.n_steps}


def _text_of(content: list[dict]) -> str:
    return "\n".join(b["text"] for b in content if b.get("type") == "text" and b.get("text"))


def _short(args: dict, n: int = 200) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return s if len(s) <= n else s[:n] + "..."
