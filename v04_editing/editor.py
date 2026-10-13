"""v04: 파일 편집 도구 (SWE-agent str_replace_editor + aider의 "did you mean" 힌트).

왜 write_file(전체 덮어쓰기)이 아닌가?
- 500줄 파일의 한 줄을 고치려고 500줄을 다시 출력하면 비싸고 느리고, 다른 줄을 실수로 바꾸기 쉽다.
- "old_str → new_str" 치환은 출력이 작고, 바뀐 부분이 명확하며, 실패를 정확히 감지할 수 있다.

설계 원칙: 실패할 때 '왜' 실패했는지, '어떻게' 고치면 되는지 모델에게 최대한 구체적으로 알려준다.
"""

import ast
import difflib
from pathlib import Path

from tools import Tool, ToolError

MAX_RESPONSE_LEN = 16000
SNIPPET_LINES = 4
TRUNCATED_NOTE = ("\n<response clipped><NOTE>To save context only part of this file was shown. "
                  "Use `grep -n` to find line numbers, then view with view_range.</NOTE>")


def numbered(text: str, start: int = 1) -> str:
    """cat -n 스타일 줄 번호. 모델이 줄 위치를 말할 수 있게 해준다."""
    return "\n".join(f"{i:6}\t{line}" for i, line in enumerate(text.split("\n"), start))


def find_similar_lines(search: str, content: str, threshold: float = 0.6) -> str:
    """old_str과 가장 비슷한 구간을 찾아 반환한다. (aider editblock_coder.find_similar_lines 이식)

    aider는 "줄 단위"로 비교한다(한 줄이 통째로 같아야 점수). 우리는 줄들을 이어붙여 "글자 단위"로
    비교해서 공백/띄어쓰기만 다른 경우도 잡는다.
    """
    search_lines, content_lines = search.splitlines(), content.splitlines()
    if not search_lines:
        return ""
    best_ratio, best_i = 0.0, -1
    for i in range(len(content_lines) - len(search_lines) + 1):
        chunk = content_lines[i:i + len(search_lines)]
        ratio = difflib.SequenceMatcher(None, "\n".join(search_lines), "\n".join(chunk)).ratio()
        if ratio > best_ratio:
            best_ratio, best_i = ratio, i
    if best_ratio < threshold:
        return ""
    lo = max(0, best_i - 2)
    hi = min(len(content_lines), best_i + len(search_lines) + 2)
    return numbered("\n".join(content_lines[lo:hi]), lo + 1)


def syntax_error(path: Path, text: str) -> str | None:
    """파이썬 파일이면 구문 오류를 검사한다. (SWE-agent는 flake8, 우리는 표준 라이브러리 ast)"""
    if path.suffix != ".py":
        return None
    try:
        ast.parse(text)
        return None
    except SyntaxError as e:
        return f"line {e.lineno}: {e.msg}"


class Editor:
    def __init__(self, root: str):
        self.root = Path(root)
        self.history: dict[Path, list[str]] = {}  # undo용: 파일별 이전 내용 스택

    # ------------------------------------------------------------------ 공통
    def _path(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.root / p

    def _read(self, p: Path) -> str:
        if not p.exists():
            raise ToolError(f"The path {p} does not exist.")
        if p.is_dir():
            raise ToolError(f"{p} is a directory.")
        with open(p, encoding="utf-8", errors="replace", newline="") as f:  # newline="": \r\n 보존
            return f.read()

    def _write(self, p: Path, new: str, old: str | None) -> None:
        if old is not None:
            self.history.setdefault(p, []).append(old)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(new)

    def _snippet(self, text: str, center_line: int, n_new_lines: int) -> str:
        lines = text.split("\n")
        lo = max(0, center_line - 1 - SNIPPET_LINES)
        hi = min(len(lines), center_line - 1 + n_new_lines + SNIPPET_LINES)
        return numbered("\n".join(lines[lo:hi]), lo + 1)

    # ------------------------------------------------------------------ 명령들
    def view(self, path: str, view_range: list[int] | None = None) -> str:
        p = self._path(path)
        if p.is_dir():
            items = sorted(x for x in p.rglob("*") if not any(part.startswith(".") for part in x.relative_to(p).parts)
                           and len(x.relative_to(p).parts) <= 2)
            listing = "\n".join(str(x.relative_to(p)) + ("/" if x.is_dir() else "") for x in items[:300])
            return f"Files and directories up to 2 levels deep in {p}:\n{listing}"
        text = self._read(p)
        start = 1
        if view_range:
            lines = text.split("\n")
            if len(view_range) != 2:
                raise ToolError("view_range must be [start, end] (end = -1 for end of file)")
            start, end = view_range
            end = len(lines) if end == -1 else end
            if not (1 <= start <= len(lines)) or end < start:
                raise ToolError(f"Invalid view_range {view_range}. The file has {len(lines)} lines.")
            text = "\n".join(lines[start - 1:end])
        out = numbered(text, start)
        return out if len(out) <= MAX_RESPONSE_LEN else out[:MAX_RESPONSE_LEN] + TRUNCATED_NOTE

    def create(self, path: str, file_text: str) -> str:
        p = self._path(path)
        if p.exists():
            raise ToolError(f"File already exists at {p}. Use str_replace to edit existing files.")
        p.parent.mkdir(parents=True, exist_ok=True)
        self._write(p, file_text, None)
        err = syntax_error(p, file_text)
        warn = f"\nWARNING: the new file has a syntax error ({err})." if err else ""
        return f"File created successfully at: {p}{warn}"

    def str_replace(self, path: str, old_str: str, new_str: str = "") -> str:
        p = self._path(path)
        content = self._read(p)
        if "\r\n" in content:  # Windows 줄바꿈 파일: 모델은 \n으로 쓰므로 맞춰준다
            old_str, new_str = old_str.replace("\r\n", "\n").replace("\n", "\r\n"), new_str.replace("\r\n", "\n").replace("\n", "\r\n")
        if old_str == new_str:
            raise ToolError("No replacement was performed: old_str and new_str are identical.")
        count = content.count(old_str)
        if count == 0:
            msg = f"No replacement was performed: old_str did not appear verbatim in {path}."
            similar = find_similar_lines(old_str, content)
            if similar:
                msg += f"\nDid you mean to match these actual lines?\n{similar}"
            if new_str and new_str in content:
                msg += "\nNote: new_str already appears in the file. Has this edit already been applied?"
            msg += "\nold_str must match exactly, including whitespace and indentation."
            raise ToolError(msg)
        if count > 1:
            line_nos = [content[:i].count("\n") + 1 for i in _find_all(content, old_str)]
            raise ToolError(f"No replacement was performed: old_str appears {count} times (lines {line_nos}). "
                            "Include more surrounding context to make it unique.")

        new_content = content.replace(old_str, new_str, 1)
        # 원래는 문법이 맞았는데 편집 후 깨졌다면 → 되돌리고 알려준다 (SWE-agent의 lint 후 revert)
        if syntax_error(p, content) is None:
            err = syntax_error(p, new_content)
            if err:
                raise ToolError(f"Edit rejected: it would introduce a Python syntax error ({err}). "
                                "The file was not changed. Fix the indentation/brackets in new_str and retry.")
        self._write(p, new_content, content)
        line = content[:content.index(old_str)].count("\n") + 1
        return (f"The file {path} has been edited. Here is a snippet of the result:\n"
                f"{self._snippet(new_content, line, new_str.count(chr(10)) + 1)}\n"
                "Review the changes and make sure they are as expected.")

    def insert(self, path: str, insert_line: int, new_str: str) -> str:
        p = self._path(path)
        content = self._read(p)
        lines = content.split("\n")
        if not 0 <= insert_line <= len(lines):
            raise ToolError(f"Invalid insert_line {insert_line}. It should be within [0, {len(lines)}].")
        new_lines = lines[:insert_line] + new_str.split("\n") + lines[insert_line:]
        new_content = "\n".join(new_lines)
        if syntax_error(p, content) is None and syntax_error(p, new_content):
            raise ToolError(f"Edit rejected: it would introduce a Python syntax error "
                            f"({syntax_error(p, new_content)}). The file was not changed.")
        self._write(p, new_content, content)
        return (f"Inserted after line {insert_line}. Snippet:\n"
                f"{self._snippet(new_content, insert_line + 1, new_str.count(chr(10)) + 1)}")

    def undo_edit(self, path: str) -> str:
        p = self._path(path)
        if not self.history.get(p):
            raise ToolError(f"No edit history found for {path}.")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(self.history[p].pop())
        return f"Last edit to {path} undone."

    # ------------------------------------------------------------------ 도구 노출
    def dispatch(self, command: str, path: str, file_text: str | None = None, old_str: str | None = None,
                 new_str: str | None = None, insert_line: int | None = None,
                 view_range: list[int] | None = None) -> str:
        if command == "view":
            return self.view(path, view_range)
        if command == "create":
            if file_text is None:
                raise ToolError("Parameter `file_text` is required for command: create")
            return self.create(path, file_text)
        if command == "str_replace":
            if old_str is None:
                raise ToolError("Parameter `old_str` is required for command: str_replace")
            return self.str_replace(path, old_str, new_str or "")
        if command == "insert":
            if insert_line is None or new_str is None:
                raise ToolError("Parameters `insert_line` and `new_str` are required for command: insert")
            return self.insert(path, insert_line, new_str)
        if command == "undo_edit":
            return self.undo_edit(path)
        raise ToolError(f"Unknown command {command!r}. Allowed: view, create, str_replace, insert, undo_edit")

    def as_tool(self) -> Tool:
        return Tool(
            "str_replace_editor",
            "View, create and edit files.\n"
            "* `view`: show a file with line numbers (optionally `view_range` [start, end], end=-1 for EOF), "
            "or list a directory 2 levels deep.\n"
            "* `create`: create a NEW file with `file_text`. Fails if the file exists.\n"
            "* `str_replace`: replace `old_str` with `new_str`. `old_str` must match EXACTLY ONE location, "
            "including whitespace. Include enough context lines to make it unique.\n"
            "* `insert`: insert `new_str` AFTER line `insert_line` (0 = top of file).\n"
            "* `undo_edit`: revert the last edit to `path`.\n"
            "Python edits that introduce syntax errors are rejected.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "enum": ["view", "create", "str_replace", "insert", "undo_edit"]},
                    "path": {"type": "string", "description": "File or directory path, relative to project root"},
                    "file_text": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                    "insert_line": {"type": "integer"},
                    "view_range": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["command", "path"],
                "additionalProperties": False,
            },
            self.dispatch,
        )


def _find_all(haystack: str, needle: str):
    i = haystack.find(needle)
    while i != -1:
        yield i
        i = haystack.find(needle, i + 1)
