"""v05: 안전장치가 들어간 에이전트.

v04 대비
- 모든 도구 호출은 실행 전에 Guard.review()를 거친다 (위험도 × 승인 모드 → 허용/질문/거부)
- 거부되면 도구를 실행하지 않고 "거부 이유(+사용자 피드백)"를 is_error 결과로 모델에게 돌려준다
- 시간 제한(wall_time_limit) 추가
"""

import os
import platform
import time
from collections.abc import Callable
from dataclasses import dataclass

from llm import LLM, Message, ToolCall, Usage
from safety import Guard
from tools import ToolRegistry

SYSTEM_PROMPT = """You are a coding agent working in a local project directory.
Use the provided tools to inspect, run and modify code. Work step by step:
explore the relevant files, make focused edits with str_replace_editor, then verify them by running code or tests.
When the task is complete, reply with a short summary of what you did and do not call any more tools.

<system_information>
{system} {release} {machine}  (project root: {cwd})
</system_information>"""


@dataclass
class AgentConfig:
    step_limit: int = 30
    wall_time_limit: int = 0  # 초, 0이면 무제한
    cwd: str = "."


class Agent:
    def __init__(self, llm: LLM, tools: ToolRegistry, config: AgentConfig, guard: Guard | None = None,
                 log: Callable[[str], None] = print):
        self.llm = llm
        self.tools = tools
        self.config = config
        self.guard = guard or Guard(mode="auto-edit", ask=None)
        self.log = log
        self.messages: list[Message] = []
        self.n_steps = 0
        self.usage = Usage()
        u = platform.uname()
        self.system = SYSTEM_PROMPT.format(system=u.system, release=u.release, machine=u.machine,
                                           cwd=os.path.abspath(config.cwd))

    def run(self, task: str) -> dict:
        self.messages = [Message("user", task)]
        start = time.time()
        while True:
            if 0 < self.config.step_limit <= self.n_steps:
                return self._exit("LimitsExceeded", f"step limit {self.config.step_limit} reached")
            if 0 < self.config.wall_time_limit <= time.time() - start:
                return self._exit("TimeExceeded", f"wall time limit {self.config.wall_time_limit}s reached")
            self.n_steps += 1
            resp = self.llm.complete(self.system, self.messages, self.tools.specs())
            self.usage.add(resp.usage)
            msg = resp.message
            self.messages.append(msg)
            if msg.content:
                self.log(f"\n[step {self.n_steps}] assistant: {msg.content}")

            if resp.stop_reason == "refusal":
                return self._exit("Refused", "The model declined this request.")
            if not msg.tool_calls:
                if resp.stop_reason == "max_tokens":
                    self.messages.append(Message("user", "Your response was cut off. Please continue."))
                    continue
                return self._exit("Done", msg.content)
            for tc in msg.tool_calls:
                self.messages.append(self._run_tool(tc, resp.stop_reason))

    def _run_tool(self, tc: ToolCall, stop_reason: str) -> Message:
        if stop_reason == "max_tokens":
            out, is_error = "Your response hit max_tokens, so this tool call was not executed. Retry with less output.", True
        elif tc.parse_error:
            out, is_error = f"Could not parse arguments for {tc.name}: {tc.parse_error}", True
        else:
            self.log(f"[tool] {tc.name}({_short(tc.arguments)})")
            out, is_error = self._guarded_run(tc)
            self.log(("[error] " if is_error else "[result] ") + out[-1500:])
        return Message("tool", out, tool_call_id=tc.id, is_error=is_error)

    def _guarded_run(self, tc: ToolCall):
        tool = self.tools.get(tc.name)
        if tool is not None:
            rv = tool.describe(tc.arguments)
            decision = self.guard.review(rv.kind, rv.key, rv.summary, rv.risk, rv.reason)
            if not decision.allowed:
                return decision.message, True
        return self.tools.run(tc.name, tc.arguments)

    def _exit(self, status: str, result: str) -> dict:
        return {"exit_status": status, "result": result, "steps": self.n_steps,
                "input_tokens": self.usage.input_tokens, "output_tokens": self.usage.output_tokens}


def _short(args: dict, n: int = 200) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return s if len(s) <= n else s[:n] + "..."
