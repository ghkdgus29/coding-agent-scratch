"""v08: 프로젝트 지침 파일(AGENTS.md 등)을 찾아 system prompt에 넣는다.

사람이 저장소에 "이 프로젝트에선 테스트를 이렇게 돌려라, 이런 스타일로 써라"를 적어두면
에이전트가 매번 같은 걸 묻거나 틀리지 않는다. (Codex: AGENTS.md, Claude Code: CLAUDE.md, Gemini CLI: GEMINI.md)

탐색 규칙: 저장소 루트(.git이 있는 곳)부터 cwd까지 내려오며 각 디렉터리의 지침 파일을 모은다.
더 깊은(구체적인) 디렉터리의 지침이 나중에 붙어서 우선한다.
"""

from pathlib import Path

INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
MAX_CHARS = 32_000


def find_repo_root(start: Path) -> Path:
    for d in [start, *start.parents]:
        if (d / ".git").exists():
            return d
    return start


def load_instructions(cwd: str) -> list[tuple[Path, str]]:
    start = Path(cwd).resolve()
    root = find_repo_root(start)
    chain = [start] + [p for p in start.parents if root in (p, *p.parents)] if start != root else [start]
    found = []
    for d in reversed(chain):  # 루트 → cwd 순서
        for name in INSTRUCTION_FILES:
            p = d / name
            if p.is_file():
                found.append((p, p.read_text(errors="replace")[:MAX_CHARS]))
                break  # 한 디렉터리에서는 하나만 (AGENTS.md 우선)
    return found


def render_instructions(cwd: str) -> str:
    docs = load_instructions(cwd)
    if not docs:
        return ""
    parts = [f'<instructions source="{p}">\n{text.strip()}\n</instructions>' for p, text in docs]
    return ("\n\n<project_instructions>\nThe user provided these project instructions. Follow them.\n"
            + "\n".join(parts) + "\n</project_instructions>")
