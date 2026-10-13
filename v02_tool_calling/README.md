# v02 — Native Tool Calling

> 목표: 텍스트 파싱 대신 API의 **구조화된 도구 호출(tool use)** 로 행동을 표현한다. 현대 코딩 에이전트(Claude Code, Codex, OpenHands, Gemini CLI)의 기본 형태다.

## 실행

```bash
uv run python main.py "fizzbuzz.py를 만들고 실행해서 확인해줘" --cwd /tmp/play
uv run python main.py "..." --cwd /tmp/play --effort medium --yes
uv run python -m unittest discover tests
```
v02는 **Anthropic 전용**이다. (OpenAI 등은 v03)

## v01 → v02 변경점

| 파일 | 변경 |
|---|---|
| `tools.py` | **새로 추가.** `Tool`(이름·설명·JSON Schema·함수) + `ToolRegistry`. 도구: `bash`, `read_file`, `write_file` |
| `agent.py` | 정규식 파싱/`FormatError`/`COMPLETE_TASK...` 제거. `tool_use` 블록을 실행하고 `tool_result`로 응답 |
| `llm.py` | `anthropic_messages()` 추가 — `tools`, `output_config.effort`, 서버 측 refusal fallback |
| `fake_llm.py` | Anthropic 응답 dict를 만드는 헬퍼 (`response`, `text`, `tool_use`) |

## 핵심 개념

### 1. 도구 = 이름 + 설명 + 입력 스키마
```python
Tool("bash", "Run a shell command ...", {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}, bash_fn)
```
요청의 `tools` 필드로 이 "메뉴판"을 보내면, 모델은 텍스트 대신 이런 블록을 돌려준다:
```json
{"type": "tool_use", "id": "toolu_01", "name": "bash", "input": {"command": "ls"}}
```
`input`은 이미 파싱된 JSON 객체다 — v01의 정규식 문제(코드블록 안의 ```, 블록 0개/2개)가 사라진다.

### 2. 루프의 모양이 바뀐다
```
assistant: [thinking, text, tool_use(A), tool_use(B)]     stop_reason = "tool_use"
user:      [tool_result(A), tool_result(B)]               ← 반드시 "한" 메시지에, 모든 id에 대해
assistant: [text "완료했습니다"]                            stop_reason = "end_turn" → 종료
```
- **종료 = 도구를 더 안 부름.** 특수 문자열이 필요 없다.
- **병렬 호출**: 한 응답에 tool_use가 여럿일 수 있다. 결과는 한 user 메시지에 모은다 (나눠 보내면 모델이 병렬 호출을 덜 하게 학습된다).
- **모든 tool_use에는 짝이 되는 tool_result가 있어야 한다.** 없으면 API가 400 에러를 낸다. (v08의 Ctrl+C 처리에서 다시 중요해진다)

### 3. 에러도 관찰이다 (`is_error: true`)
`ToolRegistry.run()`은 절대 예외를 밖으로 던지지 않는다. 파일 없음, 알 수 없는 도구, 인자 누락, 사용자 거부 — 전부 `is_error: true`인 `tool_result`로 모델에게 돌려주고, 모델이 스스로 대처하게 한다.

### 4. 응답 content는 "원본 그대로" 저장한다
`claude-opus-5-5` 같은 최신 모델은 응답에 `thinking` 블록(서명 포함)을 넣는다. 이 블록은 **다음 요청 때 수정 없이** 되돌려 보내야 모델이 이전 추론을 이어갈 수 있다. 그래서 `self.messages.append({"role": "assistant", "content": resp["content"]})` — 텍스트만 뽑아서 저장하면 안 된다. (`test_assistant_content_kept_verbatim`)

### 5. stop_reason 처리
| stop_reason | 처리 |
|---|---|
| `tool_use` | 도구 실행 후 계속 |
| `end_turn` | 종료 |
| `max_tokens` | 잘린 tool_use는 **실행하지 않고** 에러 결과로 응답 (입력이 불완전할 수 있음) |
| `refusal` | 안전 분류기가 거절. `fallbacks: "default"`를 켜두면 서버가 다른 모델로 자동 재시도하므로 드물다 |

### 6. effort
`output_config: {"effort": "high"}` — 모델이 얼마나 깊게 생각할지. `claude-opus-5-5`의 기본은 `medium`이라 코딩 에이전트에선 명시적으로 올린다. `--effort`로 조절.

## 오픈소스 대응

| 우리 코드 | 오픈소스 |
|---|---|
| `Tool`, `ToolRegistry.specs()` | OpenHands SDK [`sdk/tool/tool.py` `ToolDefinition.to_openai_tool` L757](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-sdk/openhands/sdk/tool/tool.py#L757) · Gemini CLI [`tools/tool-registry.ts` `ToolRegistry.getFunctionDeclarations` L663](https://github.com/google-gemini/gemini-cli/blob/51617c1bcbcb038291125b092501b4342f4e7f77/packages/core/src/tools/tool-registry.ts#L663) |
| `bash` 도구 스키마 | mini-swe-agent v2 [`models/utils/actions_toolcall.py` `BASH_TOOL` L11](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/utils/actions_toolcall.py#L11) — mini-swe-agent도 v2부터 텍스트 파싱 대신 tool call을 권장한다 (`actions_text.py` 상단 docstring 참고) |
| `_run_tool` → `tool_result` | mini-swe-agent `actions_toolcall.py` `parse_toolcall_actions` L30 / `format_toolcall_observation_messages` L79 |
| `ToolRegistry.run`의 에러 → 관찰 | OpenHands SDK `agent/agent.py` `_execute_action_event` L1499 (실행 예외를 `AgentErrorEvent`로 변환해 대화에 넣음) |
| 도구 미호출 = 종료 | OpenHands SDK는 명시적 [`FinishTool`](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-sdk/openhands/sdk/tool/builtins/finish.py)을 쓴다 — 두 방식의 트레이드오프를 생각해보자 (명시적 finish는 "실수로 멈춤"을 줄이지만 도구 하나가 더 필요) |
| `Agent.run` 루프 | OpenHands SDK [`agent/agent.py` `Agent.step` L694](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-sdk/openhands/sdk/agent/agent.py#L694) · Gemini CLI [`core/turn.ts` `Turn.run` L270](https://github.com/google-gemini/gemini-cli/blob/51617c1bcbcb038291125b092501b4342f4e7f77/packages/core/src/core/turn.ts#L270) |

## 한계 (→ 다음 버전)
- Anthropic 포맷에 코드가 묶여 있다. OpenAI는 `tool_calls`/`role: "tool"`로 모양이 다르다 → **v03**
- API 일시 오류(429, 529)에 바로 죽는다 → **v03 재시도**
- `write_file`은 매번 파일 전체를 다시 써야 한다 (큰 파일에서 비싸고 실수가 잦다) → **v04**

## 연습
1. `list_dir(path)` 도구를 추가해보자 (스키마 + 함수 + 테스트).
2. 도구 `description`을 일부러 모호하게 바꾸고 실제 모델로 돌려보며 행동이 어떻게 바뀌는지 관찰해보자.
