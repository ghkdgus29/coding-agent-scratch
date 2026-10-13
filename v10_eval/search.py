"""v07: 코드 검색 도구 (grep / glob) — 외부 도구(ripgrep) 없이 순수 파이썬.

bash로 `grep -rn`을 해도 되지만 전용 도구를 두는 이유:
- .git, node_modules, 빌드 산출물 같은 잡음을 기본으로 걸러준다
- 결과 개수를 제한해서 컨텍스트를 보호한다
- 읽기 전용이라 승인 없이 실행할 수 있다 (LOW)
- 플랫폼(macOS/Linux grep 옵션 차이)에 상관없이 같은 동작
"""

import fnmatch
import os
import re
from collections.abc import Iterator
from pathlib import Path

from safety import LOW
from tools import Review, Tool, ToolError, _schema

IGNORE_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", ".agent", "dist", "build",
               ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vscode", "target", ".next"}
MAX_FILE_BYTES = 1_000_000


def load_gitignore(root: Path) -> list[str]:
    p = root / ".gitignore"
    if not p.is_file():
        return []
    return [line.strip() for line in p.read_text(errors="replace").splitlines()
            if line.strip() and not line.startswith("#") and not line.startswith("!")]


def _ignored(rel: str, name: str, is_dir: bool, patterns: list[str]) -> bool:
    for pat in patterns:
        dir_only = pat.endswith("/")
        pat = pat.rstrip("/").lstrip("/")
        if dir_only and not is_dir:
            continue
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat):
            return True
    return False


def iter_files(root: Path, sub: str = ".") -> Iterator[Path]:
    """root/sub 아래 파일들을 (잡음 디렉터리와 .gitignore를 건너뛰며) 순회한다."""
    root = root.resolve()
    patterns = load_gitignore(root)
    start = (root / sub).resolve()
    if start.is_file():
        yield start
        return
    for dirpath, dirnames, filenames in os.walk(start):
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")
                             and not _ignored(os.path.normpath(os.path.join(rel_dir, d)), d, True, patterns))
        for f in sorted(filenames):
            rel = os.path.normpath(os.path.join(rel_dir, f))
            if not _ignored(rel, f, False, patterns):
                yield Path(dirpath) / f


def is_text_file(p: Path) -> bool:
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            return False
        with open(p, "rb") as f:
            return b"\0" not in f.read(1024)
    except OSError:
        return False


def grep(root: Path, pattern: str, path: str = ".", include: str | None = None, ignore_case: bool = False,
         max_results: int = 100) -> str:
    try:
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        raise ToolError(f"Invalid regex {pattern!r}: {e}") from None
    hits: list[str] = []
    n_files = 0
    for f in iter_files(root, path):
        rel = str(f.relative_to(root.resolve()))
        if include and not fnmatch.fnmatch(f.name, include) and not fnmatch.fnmatch(rel, include):
            continue
        if not is_text_file(f):
            continue
        matched = False
        with open(f, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if rx.search(line):
                    matched = True
                    hits.append(f"{rel}:{i}: {line.rstrip()[:300]}")
                    if len(hits) >= max_results:
                        return "\n".join(hits) + f"\n[stopped after {max_results} matches — narrow the pattern or path]"
        n_files += matched
    if not hits:
        return f"No matches for {pattern!r}."
    return "\n".join(hits) + f"\n[{len(hits)} matches in {n_files} files]"


def glob_files(root: Path, pattern: str, max_results: int = 200) -> str:
    """패턴에 맞는 파일 목록. 최근 수정된 파일이 먼저 (지금 작업 중인 파일일 가능성이 높다)."""
    root = root.resolve()
    files = [f for f in iter_files(root) if fnmatch.fnmatch(str(f.relative_to(root)), pattern)
             or fnmatch.fnmatch(f.name, pattern)]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return f"No files match {pattern!r}."
    lines = [str(f.relative_to(root)) for f in files[:max_results]]
    more = f"\n[{len(files) - max_results} more not shown]" if len(files) > max_results else ""
    return "\n".join(lines) + more


def search_tools(cwd: str) -> list[Tool]:
    root = Path(cwd)
    read = lambda args: Review("read", "search", f"search {args}", LOW)
    return [
        Tool("grep",
             "Search file contents with a Python regular expression. Returns `path:line: text` matches. "
             "Skips .git, node_modules, build outputs and .gitignore'd files. Use `include` (e.g. '*.py') to filter.",
             _schema({"pattern": {"type": "string"},
                      "path": {"type": "string", "description": "Directory or file to search (default: project root)"},
                      "include": {"type": "string", "description": "Glob filter on file names, e.g. '*.py'"},
                      "ignore_case": {"type": "boolean"}}, ["pattern"]),
             lambda pattern, path=".", include=None, ignore_case=False: grep(root, pattern, path, include, ignore_case),
             read),
        Tool("glob",
             "Find files by glob pattern (e.g. '**/*.py', 'tests/test_*.py', '*.md'). Most recently modified first.",
             _schema({"pattern": {"type": "string"}}, ["pattern"]),
             lambda pattern: glob_files(root, pattern), read),
    ]
