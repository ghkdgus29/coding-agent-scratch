# v03 — Provider 추상화 + 재시도

> 목표: 에이전트 코드를 특정 API 포맷에서 떼어낸다. 같은 에이전트가 Anthropic / OpenAI / OpenRouter / Ollama 위에서 돈다.

## 실행

```bash
uv run python main.py "..." --cwd /tmp/play                                  # anthropic:claude-opus-5-5 (기본)
uv run python main.py "..." --cwd /tmp/play --model openai:gpt-5             # OPENAI_API_KEY
uv run python main.py "..." --cwd /tmp/play --model openrouter:<vendor>/<model>   # OPENROUTER_API_KEY
uv run python main.py "..." --cwd /tmp/play --model ollama:qwen3-coder       # 로컬 Ollama (키 불필요)
uv run python main.py "..." --model openai:my-model --base-url http://localhost:8000/v1   # vLLM 등
uv run python -m unittest discover tests
```
> 모델 이름은 예시다. 각 provider에서 사용 가능한 실제 이름으로 바꿔 쓰자.

## v02 → v03 변경점

| 파일 | 변경 |
|---|---|
| `llm.py` → `llm/` 패키지 | `base.py`(중립 타입·HTTP·재시도), `anthropic.py`, `openai.py`, `fake.py`, `__init__.py`(`get_llm`) |
| `agent.py` | Anthropic dict 대신 `Message`/`LLMResponse`/`ToolCall` 사용. 토큰 사용량 누적 |
| `tools.py` | `to_anthropic()` → `spec()` (중립 명세, 변환은 provider 책임) |
| `fake_llm.py` → `llm/fake.py` | 중립 응답(`reply`, `call`)을 돌려주는 `FakeLLM` |

## 핵심 개념

### 1. 중립 메시지 모델
```
Message(role="user",      content="작업")
Message(role="assistant", content="설명", tool_calls=[ToolCall(id, name, arguments)], raw=<원본>)
Message(role="tool",      content="결과", tool_call_id=id, is_error=False)
```
에이전트는 이것만 안다. 각 provider는 **양방향 변환**만 책임진다:

| 중립 | Anthropic | OpenAI |
|---|---|---|
| system | 최상위 `system` | `messages[0]` role=system |
| assistant.tool_calls | content 안의 `tool_use` 블록 (`input`: 객체) | `tool_calls[].function.arguments` (**JSON 문자열**) |
| role="tool" 메시지 | `user` 메시지 안의 `tool_result` 블록. **연속된 결과는 한 메시지로 병합** | 결과마다 별도 `role: "tool"` 메시지 |
| is_error | `tool_result.is_error: true` | 필드 없음 → 내용 앞에 `ERROR:` |
| stop_reason | `end_turn`/`tool_use`/`max_tokens`/`refusal` | `stop`/`tool_calls`/`length`/`content_filter` |

### 2. `raw`: 원본을 보존하는 탈출구
추상화는 정보를 잃기 쉽다. Anthropic의 `thinking` 블록(서명 포함), OpenRouter의 `reasoning_details`는 중립 타입에 없지만 **같은 provider로 다시 보낼 때 그대로 돌려줘야** 한다. 그래서 `Message.raw`에 원본을 두고:
- 같은 provider → `raw` 재사용 (`test_raw_content_replayed_verbatim`)
- 다른 provider → 중립 필드로 재구성, provider 전용 정보는 버림 (`test_anthropic_history_to_openai`)

### 3. 실패하는 JSON
OpenAI 계열은 `arguments`를 문자열로 준다. 작은/로컬 모델은 깨진 JSON을 자주 낸다. 파싱 실패를 `ToolCall.parse_error`로 표시하고, 에이전트가 이를 `is_error` 결과로 되돌려준다 — "에러도 관찰" 원칙.

### 4. 재시도 (지수 백오프 + 지터 + retry-after)
```
시도 실패 → 재시도할 에러인가? (429, 529 overloaded, 5xx, 네트워크) ─아니오→ 바로 raise (400, 401 ...)
         └예→ delay = min(60, 2 * 2^n) * (0.5~1.0 랜덤)   ← 지터: 여러 클라이언트가 동시에 재시도하는 것 방지
              서버가 retry-after 헤더를 주면 그 값 우선
```
에이전트 루프는 수십 번 API를 호출하므로 일시 오류 한 번에 작업 전체가 날아가면 안 된다.

## 오픈소스 대응

| 우리 코드 | 오픈소스 |
|---|---|
| `get_llm("provider:model")` | mini-swe-agent [`models/__init__.py` `get_model` L45 / `get_model_class` L92](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/__init__.py#L45) (litellm / openrouter / portkey 등 클래스 선택) |
| 중립 `Message` + provider별 변환 | OpenHands SDK [`sdk/llm/message.py` `Message.to_chat_dict` L282, `to_responses_dict` L469, `from_llm_chat_message` L493](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-sdk/openhands/sdk/llm/message.py#L222) — `ThinkingBlock`(L126)을 별도 타입으로 보존하는 점이 우리 `raw`와 같은 목적 |
| `LLM.complete` 인터페이스 | Gemini CLI [`core/contentGenerator.ts` `ContentGenerator` L39](https://github.com/google-gemini/gemini-cli/blob/51617c1bcbcb038291125b092501b4342f4e7f77/packages/core/src/core/contentGenerator.ts#L39) · OpenHands `sdk/llm/llm.py` `LLM.completion` L1752 |
| `retry_call` | mini-swe-agent [`models/utils/retry.py`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/utils/retry.py) (tenacity `wait_exponential(min=4, max=60)`, 10회) · OpenHands [`llm/utils/retry_mixin.py` `retry_decorator` L77](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-sdk/openhands/sdk/llm/utils/retry_mixin.py#L77) · aider `aider/models.py` `send_completion` (L985, `RETRY_TIMEOUT=60`) |
| `FakeLLM` | Gemini CLI `core/fakeContentGenerator.ts` `FakeContentGenerator` · mini-swe-agent `DeterministicToolcallModel` |
| (참고) tool calling이 없는 모델 | OpenHands `LLM`의 `NonNativeToolCallingMixin` — 도구 호출을 텍스트 포맷으로 흉내낸다. 즉 v01 방식과 v02 방식을 모델에 따라 갈아끼우는 것 |

대부분의 프로젝트(aider, OpenHands, mini-swe-agent)는 이 계층에 **LiteLLM**을 쓴다. LiteLLM이 하는 일이 정확히 이 버전에서 직접 만든 것(포맷 변환 + 재시도 + 사용량 정규화)이다.

## 한계 (→ 다음 버전)
- `write_file`로 파일 전체를 다시 쓰는 건 큰 파일에서 비싸고 위험하다 → **v04 편집 도구**

## 연습
1. Google Gemini의 OpenAI 호환 엔드포인트를 `get_llm`에 `gemini:` provider로 추가해보자.
2. `retry_call`에 "총 대기 시간 상한"을 추가해보자.
3. 대화 중간에 모델을 바꾸는(`anthropic` → `openai`) 테스트를 만들어 `raw`가 어떻게 처리되는지 확인해보자.
