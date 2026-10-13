"""provider 중립(neutral) 타입들.

에이전트는 이 타입만 안다. Anthropic/OpenAI 포맷으로의 변환은 각 provider 모듈이 책임진다.

    Message(role="user",      content="작업 ...")
    Message(role="assistant", content="...", tool_calls=[ToolCall(...)], raw={...provider 원본...})
    Message(role="tool",      content="결과", tool_call_id="...", is_error=False)
"""

import json
import random
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field


# --------------------------------------------------------------------------- 메시지 타입
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    parse_error: str | None = None  # OpenAI는 arguments를 JSON "문자열"로 준다 → 파싱 실패 가능


@dataclass
class Message:
    role: str  # "user" | "assistant" | "tool"
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None  # role="tool"일 때 어떤 호출에 대한 결과인지
    is_error: bool = False
    # provider가 돌려준 원본 assistant 메시지. 같은 provider로 다시 보낼 때 그대로 재사용한다.
    # (Anthropic thinking 블록의 서명처럼 "손대면 안 되는" 정보를 보존하기 위함)
    raw: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> Message:
        d = dict(d)
        d["tool_calls"] = [ToolCall(**tc) for tc in d.get("tool_calls", [])]
        return Message(**d)


@dataclass
class Usage:
    """토큰 사용량. provider마다 의미가 달라서 v06부터 정규화한다.

    input_tokens       : 캐시되지 않은 입력 (Anthropic input_tokens / OpenAI prompt_tokens - cached_tokens)
    cache_read_tokens  : 캐시에서 읽은 입력 (싸다)
    cache_write_tokens : 캐시에 새로 쓴 입력 (Anthropic만)
    → 요청 하나의 실제 입력 크기 = prompt_tokens (세 값의 합)
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens


# 정규화된 stop_reason: "end_turn" | "tool_use" | "max_tokens" | "refusal" | "other"
@dataclass
class LLMResponse:
    message: Message
    stop_reason: str
    usage: Usage
    model: str = ""


class LLM:
    """모든 provider가 구현하는 인터페이스."""

    name = "base"
    model = ""

    def complete(self, system: str, messages: list[Message], tools: list[dict]) -> LLMResponse:
        raise NotImplementedError

    def stream(self, system: str, messages: list[Message], tools: list[dict],
               on_text: Callable[[str], None] | None = None) -> LLMResponse:
        """텍스트를 생성되는 대로 on_text로 흘려보내고, 끝나면 완성된 응답을 돌려준다.
        스트리밍을 지원하지 않는 provider는 기본 구현(한 번에 받아서 한 번에 출력)을 쓴다."""
        resp = self.complete(system, messages, tools)
        if on_text and resp.message.content:
            on_text(resp.message.content)
        return resp


# --------------------------------------------------------------------------- HTTP + 재시도
class APIError(Exception):
    def __init__(self, status: int, body: str, headers: dict[str, str] | None = None):
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body
        self.headers = headers or {}


def post_json(url: str, headers: dict[str, str], body: dict, timeout: float = 600) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("content-type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise APIError(e.code, e.read().decode("utf-8", errors="replace"), dict(e.headers or {})) from None


# 429 rate limit, 529 overloaded(Anthropic), 5xx 서버 오류, 408/409 일시적 충돌 → 재시도할 가치가 있다
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def is_retryable(e: Exception) -> bool:
    if isinstance(e, APIError):
        return e.status in RETRYABLE_STATUS
    return isinstance(e, (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError))


def retry_call(fn: Callable[[], dict], max_retries: int = 6, base_delay: float = 2.0, max_delay: float = 60.0,
               sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> dict:
    """지수 백오프(+지터)로 재시도. 서버가 retry-after 헤더를 주면 그것을 우선한다."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            if not is_retryable(e) or attempt >= max_retries:
                raise
            delay = min(max_delay, base_delay * (2 ** attempt)) * (0.5 + random.random() / 2)
            retry_after = getattr(e, "headers", {}).get("retry-after") if isinstance(e, APIError) else None
            if retry_after:
                try:
                    delay = min(max_delay, float(retry_after))
                except ValueError:
                    pass
            attempt += 1
            log(f"[retry {attempt}/{max_retries}] {str(e)[:120]} — {delay:.1f}s 후 재시도")
            sleep(delay)
