"""v02: 도구(tool) 정의와 레지스트리.

도구 = (이름, 설명, 입력 JSON Schema, 실제 실행할 파이썬 함수)
- 이름/설명/스키마는 LLM에게 보내는 "메뉴판"이다. 설명을 잘 쓰는 것이 프롬프트 엔지니어링의 큰 부분.
- 함수는 우리 쪽에서 실행된다. LLM은 "이 도구를 이 인자로 불러줘"라고 요청할 뿐이다.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class ToolError(Exception):
    """도구 실행 실패. 메시지는 그대로 LLM에게 is_error 결과로 전달된다."""


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., str]

    def to_anthropic(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self.tools: dict[str, Tool] = {t.name: t for t in tools}

    def specs(self) -> list[dict]:
        return [t.to_anthropic() for t in self.tools.values()]

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """(결과 텍스트, is_error). 어떤 예외도 밖으로 던지지 않는다 → 에러도 LLM에게 '관찰'로 돌려준다."""
        tool = self.tools.get(name)
        if tool is None:
            return f"Unknown tool: {name}. Available: {', '.join(self.tools)}", True
        try:
            return tool.fn(**args), False
        except ToolError as e:
            return str(e), True
        except TypeError as e:  # 필수 인자 누락 등 스키마 위반
            return f"Invalid arguments for {name}: {e}", True
        except Exception as e:
            return f"{type(e).__name__}: {e}", True


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def default_tools(cwd: str, timeout: int = 60, confirm: Callable[[str], bool] | None = None) -> ToolRegistry:
    root = Path(cwd)

    def bash(command: str) -> str:
        if confirm and not confirm(f"$ {command}"):
            raise ToolError("The user rejected this command. Try a different approach.")
        try:
            r = subprocess.run(command, shell=True, cwd=root, timeout=timeout, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors="replace")
        except subprocess.TimeoutExpired:
            raise ToolError(f"Command timed out after {timeout}s") from None
        return f"<returncode>{r.returncode}</returncode>\n<output>\n{r.stdout}</output>"

    def read_file(path: str) -> str:
        p = root / path
        if not p.is_file():
            raise ToolError(f"File not found: {path}")
        return p.read_text(errors="replace")

    def write_file(path: str, content: str) -> str:
        if confirm and not confirm(f"write {path} ({len(content)} chars)"):
            raise ToolError("The user rejected this write.")
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Wrote {len(content)} chars to {path}"

    return ToolRegistry([
        Tool("bash",
             "Run a shell command in a fresh subshell at the project root and return its exit code and combined "
             "stdout/stderr. State (cd, env vars) does not persist between calls. Do not run interactive programs.",
             _schema({"command": {"type": "string", "description": "The command to run"}}, ["command"]),
             bash),
        Tool("read_file", "Read a UTF-8 text file (path relative to project root).",
             _schema({"path": {"type": "string"}}, ["path"]), read_file),
        Tool("write_file", "Create or overwrite a file with the given full content (path relative to project root).",
             _schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]), write_file),
    ])
