"""v05: 안전장치 — 명령 위험도 분류 + 승인 정책.

두 개의 독립된 질문으로 나눈다.
  1) 이 행동은 얼마나 위험한가?       → classify_command() : LOW / MEDIUM / HIGH
  2) 이 위험도면 사람에게 물어야 하나? → Guard.review()     : 승인 모드(suggest / auto-edit / full-auto)에 따라

주의: 이건 "휴리스틱"이다. 셸 문법은 너무 강력해서 정적 분석으로 완벽히 막을 수 없다.
진짜 격리는 컨테이너/샌드박스(Docker, seatbelt, bubblewrap)가 해야 한다. 여기서는 개념을 배운다.
"""

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field

LOW, MEDIUM, HIGH = "LOW", "MEDIUM", "HIGH"
_ORDER = {LOW: 0, MEDIUM: 1, HIGH: 2}

# 읽기 전용이라 확인 없이 실행해도 되는 명령들
SAFE_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "pwd", "echo", "which", "file", "stat", "tree",
    "diff", "sort", "uniq", "nl", "du", "df", "date", "whoami", "true", "false", "basename", "dirname",
    "realpath", "printf", "cut", "tr", "env",
}
SAFE_GIT_SUBCOMMANDS = {"status", "diff", "log", "show", "blame", "ls-files", "rev-parse"}
# 에이전트 환경에선 멈춰버리는 대화형 프로그램 (SWE-agent blocklist)
INTERACTIVE = {"vim", "vi", "nvim", "nano", "emacs", "less", "more", "top", "htop", "man", "ipython", "watch"}
# 셸 연산자 (명령을 여러 조각으로 나눈다)
OPERATORS = {"&&", "||", ";", "|", "&", "|&", ";;"}


def _segments(command: str) -> list[list[str]]:
    """'a && b | c' → [['a'], ['b'], ['c']]. 파싱이 안 되면 ValueError."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    segs, cur = [], []
    for tok in lex:
        if tok in OPERATORS:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        segs.append(cur)
    return segs


def _strip_wrappers(argv: list[str]) -> list[str]:
    """'sudo rm ...', 'env X=1 rm ...', 'nohup ...' 에서 실제 명령을 찾는다."""
    while argv and (argv[0] in ("env", "nohup", "time", "nice", "command") or re.match(r"^\w+=", argv[0])):
        argv = argv[1:]
    return argv


def _is_rm_rf(argv: list[str]) -> bool:
    flags = "".join(a[1:] for a in argv[1:] if a.startswith("-") and not a.startswith("--"))
    long = {a for a in argv[1:] if a.startswith("--")}
    return ("r" in flags.lower() or "--recursive" in long) and ("f" in flags or "--force" in long)


def classify_segment(argv: list[str]) -> tuple[str, str]:
    if not argv:
        return LOW, ""
    if argv[0] == "sudo":
        return HIGH, "runs as root (sudo)"
    argv = _strip_wrappers(argv)
    if not argv:
        return LOW, ""
    cmd = argv[0].rsplit("/", 1)[-1]
    args = argv[1:]
    joined = " ".join(argv)

    if cmd == "rm" and _is_rm_rf(argv):
        targets = [a for a in args if not a.startswith("-")]
        if any(t in ("/", "~", "*", ".", "..", "$HOME") or t.startswith(("/", "~")) for t in targets):
            return HIGH, "recursive force delete of a broad or absolute path"
        return MEDIUM, "recursive force delete"
    if cmd in ("mkfs", "fdisk", "shutdown", "reboot", "halt") or cmd.startswith("mkfs."):
        return HIGH, f"system-level command ({cmd})"
    if cmd == "dd" and any(a.startswith("of=") for a in args):
        return HIGH, "raw disk write (dd of=)"
    if cmd == "chmod" and "-R" in args and "777" in args:
        return HIGH, "recursive chmod 777"
    if cmd == "git":
        sub = args[0] if args else ""
        if sub == "push" and any(a in ("-f", "--force", "--force-with-lease") for a in args):
            return HIGH, "force push"
        if sub == "reset" and "--hard" in args or sub == "clean" and any("f" in a for a in args if a.startswith("-")):
            return HIGH, "discards uncommitted work"
        if sub in SAFE_GIT_SUBCOMMANDS:
            return LOW, ""
        return MEDIUM, f"git {sub}"
    if cmd in ("curl", "wget") and re.search(r"\|\s*(ba|z)?sh\b", joined):
        return HIGH, "pipes a download into a shell"
    if cmd == "find":
        if any(a in ("-delete", "-exec", "-execdir", "-ok") or a.startswith("-fprint") for a in args):
            return MEDIUM, "find with side effects"
        return LOW, ""
    if cmd == "sed":
        return (MEDIUM, "in-place edit") if any(a.startswith("-i") for a in args) else (LOW, "")
    if cmd in SAFE_COMMANDS:
        return LOW, ""
    return MEDIUM, f"runs {cmd}"


def classify_command(command: str) -> tuple[str, str]:
    """명령 문자열 전체의 위험도 = 조각들 중 최대값."""
    if re.search(r":\(\)\s*\{", command):
        return HIGH, "fork bomb"
    if re.search(r">\s*/dev/(sd|disk|nvme)", command):
        return HIGH, "writes to a raw disk device"
    if re.search(r"curl[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b", command) or re.search(r"wget[^|]*\|\s*(ba|z)?sh\b", command):
        return HIGH, "pipes a download into a shell"
    try:
        segments = _segments(command)
    except ValueError:
        return MEDIUM, "could not parse command"
    risk, reason = LOW, ""
    for seg in segments:
        r, why = classify_segment(seg)
        if _ORDER[r] > _ORDER[risk]:
            risk, reason = r, why
    # 분석할 수 없는 구문: 명령 치환, 파일로 리다이렉트(쓰기)
    if risk == LOW and ("$(" in command or "`" in command):
        risk, reason = MEDIUM, "command substitution"
    if risk == LOW and re.search(r"(^|[^0-9&])>{1,2}\s*(?!/dev/null)[^\s&]", command):
        risk, reason = MEDIUM, "redirects output to a file"
    return risk, reason


def interactive_block_reason(command: str) -> str | None:
    """대화형 프로그램은 실행 자체를 막는다 (에이전트가 멈춰버림)."""
    try:
        segments = _segments(command)
    except ValueError:
        return None
    for seg in segments:
        seg = _strip_wrappers(seg)
        if not seg:
            continue
        cmd = seg[0].rsplit("/", 1)[-1]
        if cmd in INTERACTIVE:
            return f"Interactive program '{cmd}' is not supported here. Use non-interactive commands instead."
        if cmd == "tail" and "-f" in seg:
            return "'tail -f' never terminates. Use 'tail -n 50' instead."
        if cmd in ("python", "python3", "node", "bash", "sh", "zsh") and len(seg) == 1:
            return f"Running bare '{cmd}' would open an interactive REPL. Pass a script or use -c."
    return None


# --------------------------------------------------------------------------- 승인 정책
MODES = ("suggest", "auto-edit", "full-auto")


@dataclass
class Decision:
    allowed: bool
    message: str = ""  # 거부 시 모델에게 전달할 이유/사용자 피드백


@dataclass
class Guard:
    """도구 호출 하나를 실행해도 되는지 결정한다.

    mode
      suggest   : 읽기(LOW) 외 모든 것(편집 포함)을 물어본다
      auto-edit : 작업 폴더 안의 파일 편집은 자동, MEDIUM 이상 명령은 물어본다
      full-auto : HIGH만 물어본다
    HIGH는 어떤 모드에서도 자동 허용하지 않는다. 물어볼 수 없으면(ask=None, 비대화형) 거부한다.
    """

    mode: str = "auto-edit"
    ask: Callable[[str], str] | None = None  # 사용자에게 질문 → 답 문자열. None이면 비대화형
    always_allow: set[str] = field(default_factory=set)  # 사용자가 'a'로 영구 허용한 키 (예: "bash:pytest")
    read_only: bool = False  # v09: 계획 모드 / explore 서브 에이전트 — LOW 외에는 전부 거부

    def review(self, kind: str, key: str, summary: str, risk: str, reason: str = "") -> Decision:
        """kind: "bash" | "edit" | ..., key: 영구 허용 단위 (예: 명령 이름)."""
        if risk == LOW:
            return Decision(True)
        if self.read_only:
            return Decision(False, "Read-only mode is active: only read-only actions (viewing, searching, "
                                   "read-only commands) are allowed. Do not modify anything; report or plan instead.")
        if risk != HIGH:
            if f"{kind}:{key}" in self.always_allow or self.mode == "full-auto":
                return Decision(True)
            if kind == "edit" and self.mode == "auto-edit":
                return Decision(True)
        if self.ask is None:
            why = f" ({reason})" if reason else ""
            return Decision(False, f"Blocked: this {risk}-risk action{why} requires user approval, "
                                   "but running non-interactively. Find a safer alternative.")
        answer = self.ask(f"[{risk}{' - ' + reason if reason else ''}] {summary}\n"
                          f"  y = 허용, n = 거부, a = 이 세션에서 '{key}' 항상 허용, 그 외 텍스트 = 거부 + 모델에게 전달할 피드백\n> ")
        a = answer.strip()
        if a.lower() in ("y", "yes"):
            return Decision(True)
        if a.lower() == "a" and risk != HIGH:
            self.always_allow.add(f"{kind}:{key}")
            return Decision(True)
        if a.lower() in ("", "n", "no", "a"):
            return Decision(False, "The user denied this action. Try a different approach or ask the user.")
        return Decision(False, f"The user denied this action with feedback: {a}")
