"""v09: 계획과 분업.

1. TodoList + todo_write 도구
   긴 작업에서 모델이 "무엇을 했고 무엇이 남았는지"를 스스로 적어두는 외부 메모.
   - 대화가 압축(v06)돼도 마지막 todo 목록은 도구 결과로 남아 진행 상황이 보존된다
   - 사용자는 진행률을 볼 수 있다
2. task 도구 (서브 에이전트)
   하위 작업을 "새 대화"를 가진 별도 에이전트에게 맡기고 최종 보고만 받는다.
   - 부모의 컨텍스트가 탐색 잡음(grep 결과 수십 개)으로 오염되지 않는다
   - explore 타입은 읽기 전용이라 안전하게 맡길 수 있다
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from safety import LOW
from tools import Review, Tool, ToolError, ToolRegistry, _schema

if TYPE_CHECKING:
    from agent import Agent

STATUSES = ("pending", "in_progress", "completed")
MARK = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}


class TodoList:
    def __init__(self):
        self.items: list[dict] = []

    def write(self, todos: list[dict]) -> str:
        """목록 전체를 교체한다 (부분 수정보다 단순하고, 모델이 매번 전체 상태를 다시 생각하게 된다)."""
        clean = []
        for i, t in enumerate(todos, 1):
            if not isinstance(t, dict) or not str(t.get("content", "")).strip():
                raise ToolError(f"todo #{i} needs a non-empty `content`")
            status = t.get("status", "pending")
            if status not in STATUSES:
                raise ToolError(f"todo #{i} has invalid status {status!r}. Use one of {STATUSES}")
            clean.append({"content": str(t["content"]).strip(), "status": status})
        if sum(t["status"] == "in_progress" for t in clean) > 1:
            raise ToolError("Only one todo can be in_progress at a time. Finish or pause the current one first.")
        self.items = clean
        return self.render() + "\n\nTodos updated. Keep the list current: mark items completed as soon as they are done."

    def unfinished(self) -> list[dict]:
        return [t for t in self.items if t["status"] != "completed"]

    def render(self) -> str:
        if not self.items:
            return "(no todos)"
        done = sum(t["status"] == "completed" for t in self.items)
        lines = [f"{MARK[t['status']]} {t['content']}" for t in self.items]
        return f"Todos ({done}/{len(self.items)} done):\n" + "\n".join(lines)


def todo_tool(todo_list: TodoList, on_change: Callable[[str], None] | None = None) -> Tool:
    def todo_write(todos: list[dict]) -> str:
        out = todo_list.write(todos)
        if on_change:
            on_change(todo_list.render())
        return out

    return Tool(
        "todo_write",
        "Create or update your todo list for the current task. Use it for multi-step tasks (3+ steps): "
        "write the plan as todos first, keep exactly one item `in_progress` while working on it, and mark items "
        "`completed` immediately when done. Always send the FULL list (it replaces the previous one). "
        "Skip it for trivial single-step tasks.",
        _schema({"todos": {"type": "array", "items": {
            "type": "object",
            "properties": {"content": {"type": "string"}, "status": {"type": "string", "enum": list(STATUSES)}},
            "required": ["content", "status"], "additionalProperties": False}}}, ["todos"]),
        todo_write,
        lambda args: Review("plan", "todo", "todo_write", LOW),
    )


# --------------------------------------------------------------------------- 서브 에이전트
SUBAGENT_TYPES = {
    "explore": "Read-only investigation: find where something is implemented, how a feature works, which files "
               "are relevant. Cannot modify anything.",
    "general": "Full tool access for a self-contained sub-task (e.g. 'add tests for module X'). "
               "Edits go through the same approval policy as yours.",
}

SUBAGENT_NOTE = """

<subagent>
You are a sub-agent started by another agent to handle one focused task. You cannot ask the user questions.
Work autonomously, then finish with a concise report for the parent agent: what you found or changed,
with exact file paths, line numbers and code identifiers. The parent only sees your final message.
</subagent>"""


def read_only_registry(base: ToolRegistry) -> ToolRegistry:
    """읽기 도구만 남기고, 편집기는 view 명령만 허용하는 레지스트리."""
    keep = [t for name, t in base.tools.items() if name in ("bash", "grep", "glob", "repo_map")]
    editor = base.get("str_replace_editor")
    if editor is not None:
        inner = editor.fn

        def view_only(command: str, path: str, **kw) -> str:
            if command != "view":
                raise ToolError("This sub-agent is read-only: only the `view` command is available.")
            return inner(command=command, path=path, **kw)

        keep.insert(1, Tool("str_replace_editor", "View files (with line numbers) or list directories. Read-only.",
                            editor.input_schema, view_only, editor.review))
    return ToolRegistry(keep)


def task_tool(parent: Agent) -> Tool:
    def task(description: str, prompt: str, subagent_type: str = "explore") -> str:
        if subagent_type not in SUBAGENT_TYPES:
            raise ToolError(f"Unknown subagent_type {subagent_type!r}. Use one of {list(SUBAGENT_TYPES)}")
        result = parent.spawn_subagent(subagent_type, description, prompt)
        footer = f"\n\n[sub-agent '{description}' finished: {result['exit_status']}, {result['steps']} steps]"
        return (result["result"] or "(the sub-agent returned no report)") + footer

    types = "\n".join(f"* `{k}`: {v}" for k, v in SUBAGENT_TYPES.items())
    return Tool(
        "task",
        "Delegate a focused sub-task to a sub-agent with a fresh context. It works independently and returns only "
        "a final report, so your own context stays small. Use it for broad searches across the codebase or for "
        "self-contained sub-tasks. Write a complete, self-contained prompt: the sub-agent cannot see this "
        f"conversation.\nSub-agent types:\n{types}",
        _schema({"description": {"type": "string", "description": "3-5 word summary"},
                 "prompt": {"type": "string", "description": "Full instructions for the sub-agent"},
                 "subagent_type": {"type": "string", "enum": list(SUBAGENT_TYPES)}},
                ["description", "prompt"]),
        task,
        lambda args: Review("plan", "task", f"task: {args.get('description', '')}", LOW),
    )
