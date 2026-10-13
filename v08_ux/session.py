"""v08: 세션 저장/재개 (JSONL 이벤트 로그).

한 세션 = 한 파일 `.agent/sessions/<id>.jsonl`. 한 줄에 이벤트 하나를 "덧붙이기만" 한다.
    {"type": "meta", "id": ..., "created": ..., "model": ..., "cwd": ...}
    {"type": "message", "message": {...Message.to_dict()...}}
    {"type": "replace", "messages": [...]}          ← 압축(condense)으로 히스토리가 통째로 바뀜
    {"type": "usage", "usage": {...}, "model": ...} ← 비용 누적용

덧붙이기 전용(append-only)이라
- 프로세스가 중간에 죽어도 그때까지의 기록이 남고
- 나중에 처음부터 다시 재생(replay)하면 마지막 상태가 복원된다.
"""

import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from llm import Message, Usage

SESSIONS_DIR = ".agent/sessions"


class Session:
    def __init__(self, path: Path, session_id: str):
        self.path = path
        self.id = session_id

    @staticmethod
    def create(cwd: str, model: str) -> Session:
        sid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        path = Path(cwd) / SESSIONS_DIR / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        s = Session(path, sid)
        s._write({"type": "meta", "id": sid, "created": time.time(), "model": model, "cwd": str(Path(cwd).resolve())})
        return s

    def _write(self, event: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def add_message(self, m: Message) -> None:
        self._write({"type": "message", "message": m.to_dict()})

    def replace(self, messages: list[Message]) -> None:
        self._write({"type": "replace", "messages": [m.to_dict() for m in messages]})

    def add_usage(self, usage: Usage, model: str) -> None:
        self._write({"type": "usage", "usage": asdict(usage), "model": model})

    def load(self) -> tuple[list[Message], Usage, dict]:
        """이벤트를 처음부터 재생해서 (messages, 누적 usage, meta)를 복원한다."""
        messages: list[Message] = []
        usage, meta = Usage(), {}
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    break  # 마지막 줄이 쓰다 만 상태(크래시) → 거기까지만
                t = ev.get("type")
                if t == "meta":
                    meta = ev
                elif t == "message":
                    messages.append(Message.from_dict(ev["message"]))
                elif t == "replace":
                    messages = [Message.from_dict(d) for d in ev["messages"]]
                elif t == "usage":
                    usage.add(Usage(**ev["usage"]))
        return repair_tool_pairs(messages), usage, meta


def repair_tool_pairs(messages: list[Message]) -> list[Message]:
    """도구 호출에 결과가 없는 채로 끝났다면(실행 중 크래시) 결과를 채워 넣는다. 안 그러면 API가 400."""
    answered = {m.tool_call_id for m in messages if m.role == "tool"}
    out: list[Message] = []
    for m in messages:
        out.append(m)
        if m.role == "assistant":
            # assistant 바로 뒤에 붙여도 이어지는 tool 결과 묶음 안에 들어가므로 순서상 문제없다
            out.extend(Message("tool", "[interrupted: this tool call never completed]", tool_call_id=tc.id,
                               is_error=True) for tc in m.tool_calls if tc.id not in answered)
    return out


def list_sessions(cwd: str) -> list[Path]:
    d = Path(cwd) / SESSIONS_DIR
    return sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if d.is_dir() else []


def open_session(cwd: str, which: str) -> Session | None:
    """which = 'last' 또는 세션 id(앞부분만 써도 됨)."""
    sessions = list_sessions(cwd)
    if not sessions:
        return None
    if which == "last":
        p = sessions[0]
    else:
        matches = [p for p in sessions if p.stem.startswith(which)]
        if not matches:
            return None
        p = matches[0]
    return Session(p, p.stem)
