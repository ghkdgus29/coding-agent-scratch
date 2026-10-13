"""v08: 터미널 출력. (rich 같은 라이브러리 없이 ANSI 색상 코드만)"""

import json
import os
import sys


class UI:
    def __init__(self, color: bool | None = None):
        if color is None:
            color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        self.color = color
        self._mid_line = False

    def _c(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.color else s

    # 모델 텍스트: 스트리밍 조각을 그대로 이어서 출력
    def text(self, chunk: str) -> None:
        sys.stdout.write(chunk)
        sys.stdout.flush()
        self._mid_line = not chunk.endswith("\n")

    def end_text(self) -> None:
        if self._mid_line:
            sys.stdout.write("\n")
            self._mid_line = False

    def tool_call(self, name: str, args: dict) -> None:
        self.end_text()
        if name == "bash":
            detail = args.get("command", "")
        elif name == "str_replace_editor":
            detail = f"{args.get('command', '')} {args.get('path', '')}"
        else:
            detail = json.dumps(args, ensure_ascii=False)
        detail = detail if len(detail) <= 160 else detail[:160] + "…"
        print(self._c("36", f"⏺ {name}") + self._c("2", f" {detail}"))

    def tool_result(self, out: str, is_error: bool) -> None:
        lines = out.strip().splitlines()
        shown = "\n".join("  ⎿ " + line[:200] for line in lines[:4])
        more = f"\n  ⎿ … (+{len(lines) - 4} lines)" if len(lines) > 4 else ""
        print(self._c("31" if is_error else "2", shown + more))

    def info(self, msg: str) -> None:
        self.end_text()
        print(self._c("33", msg))

    def ask(self, prompt: str) -> str:
        self.end_text()
        return input(self._c("1;35", prompt))


class SubagentUI(UI):
    """서브 에이전트의 출력: 도구 호출만 들여써서 보여주고 텍스트는 숨긴다 (부모가 최종 보고만 받으므로)."""

    def __init__(self, parent: UI):
        super().__init__(color=parent.color)
        self.parent = parent

    def text(self, chunk):
        pass

    def end_text(self):
        pass

    def tool_call(self, name, args):
        if isinstance(self.parent, QuietUI):
            return
        sys.stdout.write("    ")
        super().tool_call(name, args)

    def tool_result(self, out, is_error):
        pass

    def info(self, msg):
        self.parent.info("    " + msg)

    def ask(self, prompt):
        return self.parent.ask(prompt)


class QuietUI(UI):
    """테스트용: 아무것도 출력하지 않는다."""

    def __init__(self):
        super().__init__(color=False)

    def text(self, chunk):
        pass

    def end_text(self):
        pass

    def tool_call(self, name, args):
        pass

    def tool_result(self, out, is_error):
        pass

    def info(self, msg):
        pass
