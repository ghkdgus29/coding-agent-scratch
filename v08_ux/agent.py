"""v08: 실사용 UX를 갖춘 에이전트.

v07 대비
- 스트리밍: 모델이 쓰는 글자가 바로 화면에 나온다 (llm.stream)
- 대화형: run_turn()을 여러 번 호출해 같은 세션에서 이어서 일한다 (REPL)
- Ctrl+C: 진행 중인 턴만 멈추고 세션은 유지. 끝나지 않은 도구 호출에는 "중단됨" 결과를 채워 넣는다
- 세션 기록: 모든 메시지를 .agent/sessions/<id>.jsonl 에 덧붙여 저장 → --resume 으로 재개
- 비용 추적 + cost_limit
- 프로젝트 지침(AGENTS.md / CLAUDE.md / GEMINI.md)을 system prompt에 포함

(v05~v07에서 쌓인 것: Guard 승인, 출력 자르기, 컨텍스트 압축, 반복 감지, repo map)
"""

import os
import platform
import time
from dataclasses import dataclass
from pathlib import Path

from context import Condenser, LoopDetector, estimate_tokens, truncate_middle
from instructions import render_instructions
from llm import LLM, APIError, LLMResponse, Message, ToolCall, Usage
from pricing import Price, cost_of
from repomap import RepoMap, mentions
from safety import Guard
from session import Session
from tools import ToolRegistry
from ui import UI

SYSTEM_PROMPT = """You are a coding agent working in a local project directory.
Use the provided tools to inspect, run and modify code. Work step by step:
use the repository map, grep and glob to find the relevant code, make focused edits with str_replace_editor,
then verify them by running code or tests.
Prefer targeted searches (grep, view_range) over dumping whole files or logs.
When the task is complete, reply with a short summary of what you did and do not call any more tools.

<system_information>
{system} {release} {machine}  (project root: {cwd})
</system_information>"""

LOOP_WARNING = ("You have called the same tool with the same arguments several times in a row. "
                "You may be stuck in a loop. Step back, reconsider your approach, and try something different.")
INTERRUPTED_RESULT = "[interrupted by the user before this tool call finished]"
OUTPUT_DIR = ".agent/outputs"


@dataclass
class AgentConfig:
    step_limit: int = 50             # 한 턴(사용자 메시지 하나)당 최대 LLM 호출 수
    wall_time_limit: int = 0
    cost_limit: float = 0.0          # 달러, 0 = 무제한 (세션 누적)
    cwd: str = "."
    max_output_chars: int = 30_000
    context_limit: int = 150_000
    repo_map_tokens: int = 2048
    stream: bool = True


def is_context_overflow(e: Exception) -> bool:
    if not isinstance(e, APIError) or e.status not in (400, 413):
        return False
    body = e.body.lower()
    return any(s in body for s in ("prompt is too long", "context_length_exceeded", "maximum context length",
                                   "context window", "too many tokens"))


class Agent:
    def __init__(self, llm: LLM, tools: ToolRegistry, config: AgentConfig, guard: Guard | None = None,
                 condenser: Condenser | None = None, session: Session | None = None, ui: UI | None = None,
                 price: Price | None = None):
        self.llm = llm
        self.tools = tools
        self.config = config
        self.ui = ui or UI()
        self.guard = guard or Guard(mode="auto-edit", ask=None)
        self.condenser = condenser or Condenser(llm, trigger_tokens=config.context_limit, log=self.ui.info)
        self.session = session
        self.price = price
        self.loops = LoopDetector()
        self.messages: list[Message] = []
        self.n_steps = 0
        self.n_outputs = 0
        self.usage = Usage()
        self.last_prompt_tokens = 0
        u = platform.uname()
        # system prompt는 세션 시작 때 한 번 만들고 고정한다 (바뀌면 프롬프트 캐시가 깨진다)
        self.system = SYSTEM_PROMPT.format(system=u.system, release=u.release, machine=u.machine,
                                           cwd=os.path.abspath(config.cwd)) + render_instructions(config.cwd)

    # ------------------------------------------------------------------ 상태
    def add(self, m: Message) -> None:
        self.messages.append(m)
        if self.session:
            self.session.add_message(m)

    def set_messages(self, messages: list[Message]) -> None:
        self.messages = messages
        if self.session:
            self.session.replace(messages)

    def resume(self, messages: list[Message], usage: Usage) -> None:
        self.messages, self.usage = messages, usage

    def total_usage(self) -> Usage:
        u = Usage()
        u.add(self.usage)
        u.add(self.condenser.usage)
        return u

    @property
    def cost(self) -> float | None:
        return cost_of(self.total_usage(), self.llm.model, self.price)

    # ------------------------------------------------------------------ 실행
    def run(self, task: str) -> dict:
        """한 번 실행하고 끝내는 모드 (v07까지와 같은 인터페이스)."""
        return self.run_turn(task)

    def initial_message(self, task: str) -> str:
        if self.config.repo_map_tokens <= 0:
            return task
        files, idents = mentions(task, Path(self.config.cwd))
        repo_map = RepoMap(self.config.cwd).get_map(self.config.repo_map_tokens, files, idents)
        if not repo_map:
            return task
        return ("<repo_map>\nThe most relevant files and definitions in this repository, ranked by importance:\n"
                f"{repo_map}\n</repo_map>\n\n<task>\n{task}\n</task>")

    def run_turn(self, user_text: str) -> dict:
        self.add(Message("user", self.initial_message(user_text) if not self.messages else user_text))
        start, steps = time.time(), 0
        try:
            while True:
                if 0 < self.config.step_limit <= steps:
                    return self._exit("LimitsExceeded", f"step limit {self.config.step_limit} reached")
                if 0 < self.config.wall_time_limit <= time.time() - start:
                    return self._exit("TimeExceeded", f"wall time limit {self.config.wall_time_limit}s reached")
                if self.config.cost_limit > 0 and (self.cost or 0) >= self.config.cost_limit:
                    return self._exit("CostLimitExceeded", f"cost limit ${self.config.cost_limit:.2f} reached")
                steps += 1
                self.n_steps += 1
                resp = self.query()  # Ctrl+C 시 부분 응답은 버린다 → 히스토리는 일관된 상태 유지
                msg = resp.message
                self.add(msg)

                if resp.stop_reason == "refusal":
                    return self._exit("Refused", "The model declined this request.")
                if not msg.tool_calls:
                    if resp.stop_reason == "max_tokens":
                        self.add(Message("user", "Your response was cut off. Please continue."))
                        continue
                    return self._exit("Done", msg.content)
                self._run_tools(msg.tool_calls, resp.stop_reason)
        except KeyboardInterrupt:
            self.ui.info("\n[interrupted]")
            return self._exit("Interrupted", "Interrupted by the user.")

    def _run_tools(self, calls: list[ToolCall], stop_reason: str) -> None:
        looping = False
        for i, tc in enumerate(calls):
            try:
                looping |= self.loops.observe(tc.name, tc.arguments)
                self.add(self._run_tool(tc, stop_reason))
            except KeyboardInterrupt:
                # 모든 tool_call에는 짝이 되는 결과가 있어야 한다 (없으면 다음 요청이 400)
                for rest in calls[i:]:
                    self.add(Message("tool", INTERRUPTED_RESULT, tool_call_id=rest.id, is_error=True))
                raise
        if looping:
            self.ui.info("[loop] repeated identical tool calls detected")
            self.add(Message("user", LOOP_WARNING))

    def query(self) -> LLMResponse:
        prompt_tokens = self.last_prompt_tokens or estimate_tokens(self.messages, self.system)
        if self.condenser.should_condense(prompt_tokens):
            self.set_messages(self.condenser.condense(self.messages, self.system))
            self.last_prompt_tokens = 0
        try:
            resp = self._call()
        except APIError as e:
            if not is_context_overflow(e):
                raise
            self.ui.info("[context] request exceeded the context window — forcing a summary and retrying")
            self.set_messages(self.condenser.condense(self.messages, self.system, force_summary=True))
            resp = self._call()
        self.usage.add(resp.usage)
        if self.session:
            self.session.add_usage(resp.usage, resp.model or self.llm.model)
        self.last_prompt_tokens = resp.usage.prompt_tokens
        return resp

    def _call(self) -> LLMResponse:
        specs = self.tools.specs()
        if self.config.stream:
            resp = self.llm.stream(self.system, self.messages, specs, on_text=self.ui.text)
        else:
            resp = self.llm.complete(self.system, self.messages, specs)
            if resp.message.content:
                self.ui.text(resp.message.content)
        self.ui.end_text()
        return resp

    def _run_tool(self, tc: ToolCall, stop_reason: str) -> Message:
        if stop_reason == "max_tokens":
            out, is_error = "Your response hit max_tokens, so this tool call was not executed. Retry with less output.", True
        elif tc.parse_error:
            out, is_error = f"Could not parse arguments for {tc.name}: {tc.parse_error}", True
        else:
            self.ui.tool_call(tc.name, tc.arguments)
            out, is_error = self._guarded_run(tc)
            out = self._truncate(out, tc.name)
            self.ui.tool_result(out, is_error)
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

    def stats(self) -> dict:
        u = self.total_usage()
        return {"steps": self.n_steps, "input_tokens": u.prompt_tokens, "output_tokens": u.output_tokens,
                "cache_read_tokens": u.cache_read_tokens, "condensations": self.condenser.n_condensations,
                "cost_usd": None if self.cost is None else round(self.cost, 6),
                "session": self.session.id if self.session else None}

    def _exit(self, status: str, result: str) -> dict:
        return {"exit_status": status, "result": result, **self.stats()}
