"""v00: LLM API를 표준 라이브러리(urllib)만으로 직접 호출한다.

핵심 포인트
- LLM API는 그냥 "JSON을 POST하면 JSON이 돌아오는" HTTP 엔드포인트다.
- LLM은 상태가 없다(stateless). 대화를 이어가려면 매번 전체 messages를 다시 보낸다.
- provider마다 요청/응답 포맷이 조금씩 다르다 (Anthropic vs OpenAI).
"""

import json
import os
import urllib.error
import urllib.request

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"


class APIError(Exception):
    """HTTP 에러를 감싼다. status 코드와 서버가 보낸 본문을 함께 보관한다."""

    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


def post_json(url: str, headers: dict[str, str], body: dict, timeout: float = 600) -> dict:
    """JSON 본문을 POST하고 JSON 응답을 dict로 돌려준다. (모든 provider 호출의 바닥)"""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("content-type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise APIError(e.code, e.read().decode("utf-8", errors="replace")) from None


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"환경변수 {name} 가 설정되어 있지 않습니다.")
    return value


# ---------------------------------------------------------------------------
# Anthropic Messages API
#   - system 프롬프트는 messages 밖의 별도 필드
#   - 응답 content는 "블록 리스트" ([{"type": "text", "text": ...}, ...])
# ---------------------------------------------------------------------------
def anthropic_chat(
    messages: list[dict],
    system: str = "",
    model: str = DEFAULT_ANTHROPIC_MODEL,
    max_tokens: int = 16000,
) -> tuple[str, dict]:
    """(응답 텍스트, 원본 응답 dict)를 반환한다."""
    headers = {
        "x-api-key": _require_env("ANTHROPIC_API_KEY"),
        "anthropic-version": "2023-06-01",
    }
    body: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        body["system"] = system
    resp = post_json(ANTHROPIC_URL, headers, body)
    # 최신 모델은 text 외에 thinking 블록도 돌려줄 수 있다. 여기서는 text만 모은다.
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    return text, resp


# ---------------------------------------------------------------------------
# OpenAI Chat Completions API (OpenRouter 등 "OpenAI 호환" 서버도 같은 포맷)
#   - system 프롬프트도 messages 안의 한 메시지 (role="system")
#   - 응답은 choices[0].message.content (문자열)
# ---------------------------------------------------------------------------
def openai_chat(
    messages: list[dict],
    system: str = "",
    model: str = "gpt-5",
    base_url: str = OPENAI_BASE_URL,
    api_key_env: str = "OPENAI_API_KEY",
    max_tokens: int | None = None,
) -> tuple[str, dict]:
    headers = {"authorization": f"Bearer {_require_env(api_key_env)}"}
    full = ([{"role": "system", "content": system}] if system else []) + messages
    body: dict = {"model": model, "messages": full}
    if max_tokens:
        # OpenAI 최신 모델은 max_tokens 대신 max_completion_tokens를 받는다.
        key = "max_completion_tokens" if "api.openai.com" in base_url else "max_tokens"
        body[key] = max_tokens
    resp = post_json(base_url.rstrip("/") + "/chat/completions", headers, body)
    text = resp["choices"][0]["message"].get("content") or ""
    return text, resp


def chat(provider: str, messages: list[dict], system: str = "", model: str | None = None) -> tuple[str, dict]:
    """provider 이름으로 분기하는 얇은 헬퍼. (v03에서 제대로 추상화한다)"""
    if provider == "anthropic":
        return anthropic_chat(messages, system, model or DEFAULT_ANTHROPIC_MODEL)
    if provider == "openai":
        return openai_chat(messages, system, model or "gpt-5")
    if provider == "openrouter":
        if not model:
            raise SystemExit("openrouter는 --model 을 지정해야 합니다 (예: anthropic/claude-...)")
        return openai_chat(messages, system, model, OPENROUTER_BASE_URL, "OPENROUTER_API_KEY")
    raise SystemExit(f"알 수 없는 provider: {provider}")


def usage_of(provider: str, resp: dict) -> tuple[int, int]:
    """(입력 토큰, 출력 토큰). 필드 이름이 provider마다 다르다는 점에 주목."""
    u = resp.get("usage") or {}
    if provider == "anthropic":
        return u.get("input_tokens", 0), u.get("output_tokens", 0)
    return u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


# ---------------------------------------------------------------------------
# v01 추가: "provider:model" 문자열로 호출 함수를 만든다.
#   에이전트는 이 함수가 어떤 provider인지 몰라도 된다: (system, messages) -> text
# ---------------------------------------------------------------------------
def parse_model_spec(spec: str) -> tuple[str, str | None]:
    """'anthropic:claude-opus-5-5' -> ('anthropic', 'claude-opus-5-5'), 'openai' -> ('openai', None)"""
    provider, _, model = spec.partition(":")
    return provider, (model or None)


def make_llm(spec: str):
    provider, model = parse_model_spec(spec)

    def call(system: str, messages: list[dict]) -> str:
        text, _ = chat(provider, messages, system, model)
        return text

    return call
