"""v06: 컨텍스트 관리.

LLM은 매 턴 전체 대화를 다시 읽는다. 대화가 길어지면
  - 비용/지연이 커지고 (입력 토큰 ∝ 대화 길이, 누적은 ∝ 길이²)
  - 모델의 컨텍스트 한도를 넘어 요청이 실패하고
  - 오래된 잡음(긴 로그) 때문에 집중력이 떨어진다.

세 단계로 대응한다.
  1. 입구에서 자르기   truncate_middle()   : 도구 출력이 너무 길면 머리+꼬리만 남기고 전문은 파일로
  2. 오래된 관찰 가리기 mask_observations() : 최근 N개 외의 도구 결과를 한 줄 요약으로 대체
  3. 요약 압축         Condenser.condense(): 과거 대화를 LLM으로 요약해 한 메시지로 대체
"""

import json
from collections.abc import Callable
from pathlib import Path

from llm import LLM, Message

TRUNCATE_NOTICE = "\n\n<response clipped: {omitted} characters omitted from the middle>\n\n"
TRUNCATE_NOTICE_SAVED = ("\n\n<response clipped: {omitted} characters omitted. Full output saved to {path} "
                         "— view it with str_replace_editor (view_range) or grep, starting around line {line}>\n\n")


# --------------------------------------------------------------------------- 1. 입구에서 자르기
def truncate_middle(text: str, limit: int, save_path: Path | None = None, display_path: str = "") -> str:
    """앞뒤를 남기고 가운데를 자른다. 에러 메시지는 보통 끝에, 명령 맥락은 앞에 있기 때문."""
    if limit <= 0 or len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    omitted = len(text) - head - tail
    if save_path is not None:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(text)
        notice = TRUNCATE_NOTICE_SAVED.format(omitted=omitted, path=display_path or save_path,
                                              line=text[:head].count("\n") + 1)
    else:
        notice = TRUNCATE_NOTICE.format(omitted=omitted)
    return text[:head] + notice + text[-tail:]


# --------------------------------------------------------------------------- 토큰 추정
def estimate_tokens(messages: list[Message], system: str = "") -> int:
    """대략 4글자 = 1토큰. 정확한 값은 API usage가 알려주므로 추정은 보조용."""
    chars = len(system)
    for m in messages:
        chars += len(m.content) + sum(len(tc.name) + len(json.dumps(tc.arguments)) for tc in m.tool_calls)
    return chars // 4


# --------------------------------------------------------------------------- 2. 관찰 가리기
def tool_names_by_id(messages: list[Message]) -> dict[str, str]:
    return {tc.id: tc.name for m in messages if m.role == "assistant" for tc in m.tool_calls}


def mask_observations(messages: list[Message], keep_last: int = 5) -> list[Message]:
    """최근 keep_last개를 제외한 도구 결과를 한 줄 자리표시자로 바꾼다. (SWE-agent LastNObservations)"""
    names = tool_names_by_id(messages)
    tool_idx = [i for i, m in enumerate(messages) if m.role == "tool"]
    to_mask = set(tool_idx[:-keep_last] if keep_last > 0 else tool_idx)
    out = []
    for i, m in enumerate(messages):
        if i in to_mask and not m.content.startswith("[Old output"):
            n_lines = m.content.count("\n") + 1
            placeholder = (f"[Old output of {names.get(m.tool_call_id, 'tool')} omitted ({n_lines} lines). "
                           "Re-run the tool if you need it again.]")
            m = Message("tool", placeholder, tool_call_id=m.tool_call_id, is_error=m.is_error)
        out.append(m)
    return out


def strip_provider_raw(messages: list[Message]) -> list[Message]:
    """히스토리를 편집했다면 provider 원본(raw, 특히 thinking 블록)을 모두 버린다.

    최신 Claude 모델의 thinking 블록 서명은 "그 블록 이전의 대화 전체"에 묶여 있다.
    앞부분을 바꾼 뒤 뒤쪽 thinking 블록을 그대로 다시 보내면 검증에 실패한다(400).
    thinking 블록을 하나도 보내지 않으면 검증할 것도 없다 → 안전.
    (대가: 모델이 이전 턴의 내부 추론을 잃는다. 대화 텍스트와 도구 기록은 남는다.)
    """
    return [Message(m.role, m.content, m.tool_calls, m.tool_call_id, m.is_error, None) if m.raw else m
            for m in messages]


# --------------------------------------------------------------------------- 3. 요약 압축
SUMMARY_SYSTEM = """You are maintaining a context-aware state summary for a coding agent.
You will be given the earlier part of the agent's conversation (which may include a previous summary).
Write a concise summary that lets the agent continue the task without the original messages.

Use these sections (skip empty ones):
USER_CONTEXT: essential requirements, goals and clarifications from the user
COMPLETED: what has been done so far, with brief results
PENDING: what still needs to be done
CODE_STATE: relevant file paths, functions, data structures
CHANGES: code edits made so far (file + what changed)
TESTS: test commands, failing cases, error messages
NOTES: anything else that is important (gotchas, decisions, commands that did not work)"""


def render_transcript(messages: list[Message], max_tool_chars: int = 2000) -> str:
    parts = []
    for m in messages:
        if m.role == "tool":
            body = m.content if len(m.content) <= max_tool_chars else m.content[:max_tool_chars] + " ...[truncated]"
            parts.append(f"<TOOL_RESULT{' error' if m.is_error else ''}>\n{body}\n</TOOL_RESULT>")
        elif m.role == "assistant":
            calls = "".join(f"\n-> {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)[:1000]})"
                            for tc in m.tool_calls)
            parts.append(f"<ASSISTANT>\n{m.content}{calls}\n</ASSISTANT>")
        else:
            parts.append(f"<USER>\n{m.content}\n</USER>")
    return "\n".join(parts)


class Condenser:
    """컨텍스트가 trigger_tokens를 넘으면 (1) 관찰 마스킹 → (2) 그래도 크면 LLM 요약."""

    def __init__(self, llm: LLM, trigger_tokens: int = 150_000, target_ratio: float = 0.5,
                 keep_observations: int = 5, keep_tail: int = 6, log: Callable[[str], None] = print):
        self.llm = llm
        self.trigger_tokens = trigger_tokens
        self.target_tokens = int(trigger_tokens * target_ratio)
        self.keep_observations = keep_observations
        self.keep_tail = keep_tail
        self.log = log
        self.n_condensations = 0

    def should_condense(self, prompt_tokens: int) -> bool:
        return self.trigger_tokens > 0 and prompt_tokens >= self.trigger_tokens

    def condense(self, messages: list[Message], system: str = "", force_summary: bool = False) -> list[Message]:
        before = estimate_tokens(messages, system)
        masked = mask_observations(messages, self.keep_observations)
        if not force_summary and estimate_tokens(masked, system) <= self.target_tokens:
            self.log(f"[context] masked old observations: ~{before} → ~{estimate_tokens(masked, system)} tokens")
            self.n_condensations += 1
            return strip_provider_raw(masked)
        summarized = self.summarize(messages)
        self.log(f"[context] summarized history: ~{before} → ~{estimate_tokens(summarized, system)} tokens")
        self.n_condensations += 1
        return strip_provider_raw(summarized)

    def tail_start(self, messages: list[Message]) -> int:
        """남길 꼬리의 시작 위치. 도구 호출과 그 결과 쌍을 절대 쪼개지 않도록 assistant 메시지에서 시작한다."""
        s = max(1, len(messages) - self.keep_tail)
        while s > 1 and messages[s].role != "assistant":
            s -= 1
        return s

    def summarize(self, messages: list[Message]) -> list[Message]:
        s = self.tail_start(messages)
        head, middle, tail = messages[:1], messages[1:s], messages[s:]
        if not middle:
            return mask_observations(messages, self.keep_observations)
        resp = self.llm.complete(SUMMARY_SYSTEM, [Message("user", render_transcript(middle) +
                                                          "\n\nNow write the summary.")], [])
        summary = Message("user", "<summary_of_earlier_conversation>\n"
                                  f"{resp.message.content}\n</summary_of_earlier_conversation>\n"
                                  "Continue the task from where it was left off.")
        return head + [summary] + tail


# --------------------------------------------------------------------------- 반복 감지
class LoopDetector:
    """같은 도구 호출(이름+인자)이 threshold번 연속되면 경고한다. (Gemini CLI loopDetectionService)"""

    def __init__(self, threshold: int = 4):
        self.threshold = threshold
        self.last_key = ""
        self.count = 0

    def observe(self, name: str, arguments: dict) -> bool:
        key = name + json.dumps(arguments, sort_keys=True)
        self.count = self.count + 1 if key == self.last_key else 1
        self.last_key = key
        return self.count >= self.threshold
