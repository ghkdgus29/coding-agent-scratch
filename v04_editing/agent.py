"""v03: provider 중립 에이전트.

v02와 루프 구조는 같다. 다만 Anthropic dict 대신 llm.Message / LLMResponse 를 다룬다.
그래서 같은 에이전트 코드가 Anthropic, OpenAI, OpenRouter, Ollama 모두에서 동작한다.
"""

import os
import platform
from collections.abc import Callable
from dataclasses import dataclass

from llm import LLM, Message, ToolCall, Usage
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
    cwd: str = "."


class Agent:
    def __init__(self, llm: LLM, tools: ToolRegistry, config: AgentConfig, log: Callable[[str], None] = print):
        self.llm = llm
        self.tools = tools
        self.config = config
        self.log = log
        self.messages: list[Message] = []
        self.n_steps = 0
        self.usage = Usage()
        u = platform.uname()
        self.system = SYSTEM_PROMPT.format(system=u.system, release=u.release, machine=u.machine,
                                           cwd=os.path.abspath(config.cwd))

    def run(self, task: str) -> dict:
        self.messages = [Message("user", task)]
        while True:
            if 0 < self.config.step_limit <= self.n_steps:
                return self._exit("LimitsExceeded", f"step limit {self.config.step_limit} reached")
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
            out, is_error = self.tools.run(tc.name, tc.arguments)
            self.log(("[error] " if is_error else "[result] ") + out[-1500:])
        return Message("tool", out, tool_call_id=tc.id, is_error=is_error)

    def _exit(self, status: str, result: str) -> dict:
        return {"exit_status": status, "result": result, "steps": self.n_steps,
                "input_tokens": self.usage.input_tokens, "output_tokens": self.usage.output_tokens}


def _short(args: dict, n: int = 200) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return s if len(s) <= n else s[:n] + "..."
