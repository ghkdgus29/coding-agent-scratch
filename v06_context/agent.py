"""v06: 컨텍스트를 관리하는 에이전트.

v05 대비
- 모든 도구 출력은 max_output_chars를 넘으면 머리+꼬리만 남기고, 전문은 .agent/outputs/ 에 저장
- 매 LLM 호출 전에 직전 요청의 실제 입력 토큰(API usage)을 보고, 한도를 넘었으면 Condenser로 압축
- 요청이 "컨텍스트 초과"로 실패하면 강제 요약 후 한 번 재시도
- 같은 도구 호출이 반복되면 경고 메시지를 넣는다 (LoopDetector)
"""

import os
import platform
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from context import Condenser, LoopDetector, estimate_tokens, truncate_middle
from llm import LLM, APIError, LLMResponse, Message, ToolCall, Usage
from safety import Guard
from tools import ToolRegistry

SYSTEM_PROMPT = """You are a coding agent working in a local project directory.
Use the provided tools to inspect, run and modify code. Work step by step:
explore the relevant files, make focused edits with str_replace_editor, then verify them by running code or tests.
Prefer targeted commands (grep -n, view_range, head) over dumping whole files or logs.
When the task is complete, reply with a short summary of what you did and do not call any more tools.

<system_information>
{system} {release} {machine}  (project root: {cwd})
</system_information>"""

LOOP_WARNING = ("You have called the same tool with the same arguments several times in a row. "
                "You may be stuck in a loop. Step back, reconsider your approach, and try something different.")
OUTPUT_DIR = ".agent/outputs"


@dataclass
class AgentConfig:
    step_limit: int = 50
    wall_time_limit: int = 0
    cwd: str = "."
    max_output_chars: int = 30_000   # 도구 출력 하나의 최대 길이 (OpenHands MAX_CMD_OUTPUT_SIZE)
    context_limit: int = 150_000     # 이 토큰 수를 넘으면 압축 (0 = 끔)


def is_context_overflow(e: Exception) -> bool:
    if not isinstance(e, APIError) or e.status not in (400, 413):
        return False
    body = e.body.lower()
    return any(s in body for s in ("prompt is too long", "context_length_exceeded", "maximum context length",
                                   "context window", "too many tokens"))


class Agent:
    def __init__(self, llm: LLM, tools: ToolRegistry, config: AgentConfig, guard: Guard | None = None,
                 condenser: Condenser | None = None, log: Callable[[str], None] = print):
        self.llm = llm
        self.tools = tools
        self.config = config
        self.guard = guard or Guard(mode="auto-edit", ask=None)
        self.condenser = condenser or Condenser(llm, trigger_tokens=config.context_limit, log=log)
        self.loops = LoopDetector()
        self.log = log
        self.messages: list[Message] = []
        self.n_steps = 0
        self.n_outputs = 0
        self.usage = Usage()
        self.last_prompt_tokens = 0
        u = platform.uname()
        self.system = SYSTEM_PROMPT.format(system=u.system, release=u.release, machine=u.machine,
                                           cwd=os.path.abspath(config.cwd))

    # ------------------------------------------------------------------ 루프
    def run(self, task: str) -> dict:
        self.messages = [Message("user", task)]
        start = time.time()
        while True:
            if 0 < self.config.step_limit <= self.n_steps:
                return self._exit("LimitsExceeded", f"step limit {self.config.step_limit} reached")
            if 0 < self.config.wall_time_limit <= time.time() - start:
                return self._exit("TimeExceeded", f"wall time limit {self.config.wall_time_limit}s reached")
            self.n_steps += 1
            resp = self.query()
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
            looping = False
            for tc in msg.tool_calls:
                looping |= self.loops.observe(tc.name, tc.arguments)
                self.messages.append(self._run_tool(tc, resp.stop_reason))
            if looping:
                self.log("[loop] repeated identical tool calls detected")
                self.messages.append(Message("user", LOOP_WARNING))

    def query(self) -> LLMResponse:
        # 직전 요청의 실제 입력 크기(없으면 추정치)로 압축 여부 결정
        prompt_tokens = self.last_prompt_tokens or estimate_tokens(self.messages, self.system)
        if self.condenser.should_condense(prompt_tokens):
            self.messages = self.condenser.condense(self.messages, self.system)
            self.last_prompt_tokens = 0
        try:
            resp = self.llm.complete(self.system, self.messages, self.tools.specs())
        except APIError as e:
            if not is_context_overflow(e):
                raise
            self.log("[context] request exceeded the context window — forcing a summary and retrying")
            self.messages = self.condenser.condense(self.messages, self.system, force_summary=True)
            resp = self.llm.complete(self.system, self.messages, self.tools.specs())
        self.usage.add(resp.usage)
        self.last_prompt_tokens = resp.usage.prompt_tokens
        return resp

    # ------------------------------------------------------------------ 도구 실행
    def _run_tool(self, tc: ToolCall, stop_reason: str) -> Message:
        if stop_reason == "max_tokens":
            out, is_error = "Your response hit max_tokens, so this tool call was not executed. Retry with less output.", True
        elif tc.parse_error:
            out, is_error = f"Could not parse arguments for {tc.name}: {tc.parse_error}", True
        else:
            self.log(f"[tool] {tc.name}({_short(tc.arguments)})")
            out, is_error = self._guarded_run(tc)
            out = self._truncate(out, tc.name)
            self.log(("[error] " if is_error else "[result] ") + out[-1500:])
        return Message("tool", out, tool_call_id=tc.id, is_error=is_error)

    def _truncate(self, out: str, tool_name: str) -> str:
        if len(out) <= self.config.max_output_chars:
            return out
        self.n_outputs += 1
        rel = f"{OUTPUT_DIR}/{tool_name}_{self.n_outputs}.txt"
        return truncate_middle(out, self.config.max_output_chars, Path(self.config.cwd) / rel, rel)

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
                "input_tokens": self.usage.prompt_tokens, "output_tokens": self.usage.output_tokens,
                "cache_read_tokens": self.usage.cache_read_tokens, "condensations": self.condenser.n_condensations}


def _short(args: dict, n: int = 200) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return s if len(s) <= n else s[:n] + "..."
