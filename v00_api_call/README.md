# v00 — LLM API 직접 호출하기

> 목표: 코딩 에이전트의 "엔진"인 LLM API가 실제로 어떻게 생겼는지 SDK 없이 확인한다.

## 실행

```bash
export ANTHROPIC_API_KEY=...
uv run python chat.py                         # 멀티턴 채팅
uv run python chat.py -p "안녕?" --show-request
uv run python chat.py --provider openai --model gpt-5          # OPENAI_API_KEY
uv run python chat.py --provider openrouter --model <slug>     # OPENROUTER_API_KEY
uv run python -m unittest discover tests      # API 키 없이 테스트
```

## 핵심 개념

| 개념 | 설명 |
|---|---|
| **HTTP POST + JSON** | LLM API는 JSON을 보내면 JSON이 돌아오는 엔드포인트일 뿐이다. `llm.post_json()` 20줄이면 충분하다. |
| **messages** | `[{"role": "user", "content": ...}, {"role": "assistant", ...}, ...]` 의 리스트. 대화 전체다. |
| **stateless** | 서버는 이전 대화를 기억하지 않는다. `chat.py`의 `history` 리스트를 **매번 통째로 다시 보내는 것**이 "대화"의 정체다. `--show-request`로 messages 수가 늘어나는 것을 확인해보자. |
| **system prompt** | 모델의 역할/규칙. Anthropic은 별도 `system` 필드, OpenAI는 `role="system"` 메시지. |
| **토큰 / usage** | 비용과 컨텍스트 한도의 단위. 응답의 `usage`에 들어있다. 매 턴 전체 history를 보내므로 **입력 토큰이 턴마다 누적 증가**한다 → v06에서 다룰 문제. |
| **stop_reason** | 왜 생성이 멈췄나 (`end_turn`, `max_tokens`, 이후 `tool_use`). v02부터 핵심이 된다. |

### Provider별 포맷 차이 (v03에서 추상화할 대상)

| | Anthropic Messages | OpenAI Chat Completions (+ OpenRouter 등 호환 서버) |
|---|---|---|
| 엔드포인트 | `POST /v1/messages` | `POST {base_url}/chat/completions` |
| 인증 헤더 | `x-api-key`, `anthropic-version: 2023-06-01` | `Authorization: Bearer ...` |
| system | 최상위 `system` 필드 | `messages[0] = {"role":"system"}` |
| 응답 본문 | `content`: 블록 리스트 (`text`, `thinking`, `tool_use`...) | `choices[0].message.content`: 문자열 |
| 토큰 | `usage.input_tokens / output_tokens` | `usage.prompt_tokens / completion_tokens` |
| 출력 상한 | `max_tokens` (필수) | `max_completion_tokens` (OpenAI 최신) / `max_tokens` |

> 참고: 최신 Claude 모델(기본값 `claude-opus-5-5`)은 응답에 `thinking` 블록을 포함할 수 있다. v00은 text만 뽑지만, **에이전트에서는 이 블록을 수정 없이 다시 보내야 한다** — v02/v03에서 다룬다.

### curl로 보면

```bash
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-opus-5-5","max_tokens":1024,"messages":[{"role":"user","content":"hi"}]}'
```
`llm.anthropic_chat()`은 이 curl을 파이썬으로 옮긴 것이다.

## 오픈소스 대응

대부분의 오픈소스 에이전트는 이 계층을 **LiteLLM** 같은 라이브러리에 맡긴다. 우리는 직접 만든다.

| 우리 코드 | 오픈소스 |
|---|---|
| `llm.post_json`, `anthropic_chat`, `openai_chat` | mini-swe-agent [`models/litellm_model.py` `LitellmModel._query`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/litellm_model.py#L64) — `litellm.completion()` 한 줄로 모든 provider 호출 |
| `usage_of` (provider별 필드 차이) | mini-swe-agent `LitellmModel._calculate_cost` (L108) — litellm이 정규화한 usage로 비용 계산 |
| `APIError` | aider [`aider/models.py` `send_completion`](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/models.py#L985) — litellm 예외 처리 |

## 연습

1. `chat.py`에서 `history.append({"role": "assistant", ...})`를 지우면 어떻게 되는가? (모델이 자기 답을 "기억"하지 못한다)
2. `--show-request`를 켜고 10턴 대화한 뒤, `[tokens in=...]`이 어떻게 증가하는지 관찰해보자.

## 다음: v01
LLM이 텍스트만 돌려줄 수 있다면, 어떻게 "명령을 실행"하게 할까? → **응답 속 bash 코드블록을 우리가 대신 실행해주면 된다.**
