"""도구 정의와 레지스트리.

v05 변경
- Tool.review: 실행 전에 "무엇을/얼마나 위험하게" 하려는지 설명하는 함수 (Guard가 승인 판단에 사용)
- bash: 대화형 프로그램 차단, stdin 닫기, 페이저 끄기, 타임아웃 시 프로세스 그룹 전체 kill
- 확인(confirm) 콜백은 사라지고 에이전트의 Guard가 모든 도구를 일괄 심사한다
"""

import os
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from safety import MEDIUM, classify_command, interactive_block_reason


class ToolError(Exception):
    """도구 실행 실패. 메시지는 그대로 LLM에게 is_error 결과로 전달된다."""


class Review(NamedTuple):
    kind: str      # "bash" | "edit" | "read" | 도구 이름
    key: str       # '항상 허용'의 단위 (예: bash면 명령 이름)
    summary: str   # 사람에게 보여줄 한 줄 요약
    risk: str      # LOW | MEDIUM | HIGH
    reason: str = ""


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., str]
    review: Callable[[dict], Review] | None = None

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    def describe(self, args: dict) -> Review:
        if self.review:
            return self.review(args)
        return Review(self.name, self.name, f"{self.name}({args})", MEDIUM)


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self.tools: dict[str, Tool] = {t.name: t for t in tools}

    def specs(self) -> list[dict]:
        return [t.spec() for t in self.tools.values()]

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        tool = self.tools.get(name)
        if tool is None:
            return f"Unknown tool: {name}. Available: {', '.join(self.tools)}", True
        try:
            return tool.fn(**args), False
        except ToolError as e:
            return str(e), True
        except TypeError as e:
            return f"Invalid arguments for {name}: {e}", True
        except Exception as e:
            return f"{type(e).__name__}: {e}", True


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


# 페이저/진행바/색상 때문에 출력이 지저분해지거나 멈추는 것을 막는다 (mini-swe-agent 기본 env)
AGENT_ENV = {"PAGER": "cat", "MANPAGER": "cat", "GIT_PAGER": "cat", "LESS": "-R", "PIP_PROGRESS_BAR": "off",
             "TQDM_DISABLE": "1", "NO_COLOR": "1", "TERM": "dumb", "GIT_TERMINAL_PROMPT": "0"}


def run_shell(command: str, cwd: Path, timeout: int) -> tuple[int, str, bool]:
    """(returncode, output, timed_out). 타임아웃이면 자식 프로세스까지 프로세스 그룹째 죽인다."""
    proc = subprocess.Popen(
        command, shell=True, cwd=cwd, env={**os.environ, **AGENT_ENV},
        stdin=subprocess.DEVNULL,           # 입력 대기로 멈추는 것 방지 (예: 'Continue? [y/N]')
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
        start_new_session=True,             # 새 프로세스 그룹 → 그룹 전체를 kill 할 수 있다
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out, False
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, _ = proc.communicate()
        return -1, out or "", True
    except KeyboardInterrupt:
        # 새 세션으로 띄운 자식은 터미널의 Ctrl+C(SIGINT)를 받지 못한다 → 우리가 직접 정리
        _kill_group(proc)
        proc.communicate()
        raise


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def bash_tool(cwd: str, timeout: int = 60) -> Tool:
    root = Path(cwd)

    def bash(command: str) -> str:
        blocked = interactive_block_reason(command)
        if blocked:
            raise ToolError(blocked)
        code, out, timed_out = run_shell(command, root, timeout)
        if timed_out:
            raise ToolError(f"Command timed out after {timeout}s and was killed. Partial output:\n{out[-2000:]}")
        return f"<returncode>{code}</returncode>\n<output>\n{out}</output>"

    def review(args: dict) -> Review:
        command = args.get("command", "")
        risk, reason = classify_command(command)
        first = command.strip().split()[0] if command.strip() else ""
        return Review("bash", first, f"$ {command}", risk, reason)

    return Tool("bash",
                "Run a shell command in a fresh subshell at the project root and return its exit code and combined "
                "stdout/stderr. State (cd, env vars) does not persist between calls. stdin is closed and "
                f"commands time out after {timeout}s. Interactive programs (vim, less, bare python) are blocked. "
                "Use the str_replace_editor tool (not sed/cat) to view and edit files, and grep/glob to search.",
                _schema({"command": {"type": "string", "description": "The command to run"}}, ["command"]),
                bash, review)


def repo_map_tool(cwd: str) -> Tool:
    from repomap import RepoMap
    from safety import LOW

    rm = RepoMap(cwd)

    def repo_map(focus_files: list[str] | None = None, max_tokens: int = 2048) -> str:
        return rm.get_map(min(max_tokens, 8192), mentioned_files=set(focus_files or []))

    return Tool("repo_map",
                "Show a ranked map of the repository: the most important files and their class/function signatures. "
                "Pass `focus_files` to rank code related to those files higher.",
                _schema({"focus_files": {"type": "array", "items": {"type": "string"}},
                         "max_tokens": {"type": "integer"}}, []),
                repo_map, lambda args: Review("read", "repo_map", "repo_map", LOW))


def default_tools(cwd: str, timeout: int = 60) -> ToolRegistry:
    from editor import Editor  # 순환 import 방지
    from search import search_tools

    return ToolRegistry([bash_tool(cwd, timeout), Editor(cwd).as_tool(), *search_tools(cwd), repo_map_tool(cwd)])
