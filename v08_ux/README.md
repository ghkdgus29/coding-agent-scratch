# v08 — 실사용 UX: 스트리밍 · 대화형 · 중단 · 세션 · 비용

> 목표: "한 번 실행하고 결과 JSON을 보는 스크립트"를 매일 쓸 수 있는 도구로 만든다.

## 실행

```bash
uv run python main.py --cwd ~/proj                          # 대화형 REPL (/help, /cost, /compact, /sessions, /exit)
uv run python main.py "테스트 고쳐줘" --cwd ~/proj            # 한 번 실행
uv run python main.py --cwd ~/proj --resume last            # 마지막 세션 이어서
uv run python main.py --cwd ~/proj --sessions               # 세션 목록
uv run python main.py "..." --cost-limit 2.0                # $2 넘으면 멈춤
uv run python main.py "..." --model openai:gpt-5 --price 1.25,10   # 가격표에 없는 모델의 가격 지정
uv run python main.py "..." --yes --stats-json out.json     # 비대화형 + 통계 저장 (v10 평가에서 사용)
uv run python -m unittest discover tests
```

## v07 → v08 변경점

| 파일 | 변경 |
|---|---|
| `llm/stream.py` | **새로 추가.** SSE 파서 + Anthropic/OpenAI 스트림 누적기 |
| `llm/base.py`, `anthropic.py`, `openai.py` | `LLM.stream(on_text=...)`. Anthropic: **자동 프롬프트 캐싱**(`cache_control`), 스트리밍 시 `max_tokens=64000` |
| `session.py` | **새로 추가.** JSONL 이벤트 로그 저장/재생, 크래시 복구(`repair_tool_pairs`) |
| `pricing.py` | **새로 추가.** 모델별 단가 → 비용 |
| `instructions.py` | **새로 추가.** `AGENTS.md`/`CLAUDE.md`/`GEMINI.md` 탐색 → system prompt |
| `ui.py` | **새로 추가.** 스트리밍 출력, 도구 호출/결과 요약, 색상 |
| `agent.py` | `run_turn()`(멀티턴), Ctrl+C 처리, 세션 기록, `cost_limit`, 통계 |
| `context.py` | 요약 호출의 토큰도 집계 (`Condenser.usage`) |
| `tools.py` | Ctrl+C 시 자식 프로세스 그룹도 정리 |
| `main.py` | REPL, `--resume`, `--sessions`, `--cost-limit`, `--price`, `--stats-json`, `--no-stream` |

## 핵심 개념

### 1. 스트리밍 = SSE 파싱 + 누적
```
event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"Hel"}}
                                                                ↓ on_text("Hel") → 화면
event: content_block_delta
data: {..."delta":{"type":"input_json_delta","partial_json":"{\"comm"}}   ← 도구 인자는 JSON 조각으로 온다
```
- 조각들을 모아 **비스트리밍 응답과 똑같은 dict**를 만든 뒤 v03의 `from_anthropic_response`를 재사용한다. 스트리밍은 "전송 방식"일 뿐 결과 형식은 같다.
- 도구 인자 JSON은 `content_block_stop`에서 한 번에 파싱. 잘렸으면(`max_tokens`) `parse_error` → 실행 안 함.
- `thinking` 블록의 `signature_delta`도 빠짐없이 모아야 다음 요청에 원본 그대로 돌려줄 수 있다.
- 스트림 중간의 `error` 이벤트(`overloaded_error`)는 `APIError(529)`로 바꿔 v03의 재시도 로직을 그대로 탄다.

### 2. Ctrl+C: 턴만 멈추고 세션은 살린다
| 언제 | 처리 |
|---|---|
| LLM 응답 중 | 부분 응답을 **버린다** → 히스토리는 직전의 일관된 상태 |
| 도구 실행 중 | 현재+남은 tool call 모두에 `"[interrupted by the user ...]"` 결과를 채운다 → **모든 tool_call에 짝이 있다** (없으면 다음 요청이 400) |
| bash 실행 중 | 자식은 새 세션이라 SIGINT를 못 받는다 → 우리가 `killpg` |
| 크래시로 세션 파일이 끊김 | 재개 시 `repair_tool_pairs`가 짝 없는 호출에 결과를 채운다 |

### 3. 세션 = 덧붙이기 전용 이벤트 로그
`.agent/sessions/<id>.jsonl` 에 `meta` / `message` / `replace`(압축) / `usage` 이벤트를 한 줄씩 append. 재개 = 처음부터 재생. 쓰다 만 마지막 줄은 무시. 데이터베이스 없이도 크래시에 강하다.

### 4. 비용과 캐시
- 토큰 × 단가. `cache_read`는 입력 단가의 1/10~1/20 이라 **캐시 적중률이 곧 비용**이다. `/cost`의 `cached` 숫자를 보자.
- 캐시가 잘 맞으려면 앞부분이 바뀌면 안 된다: system prompt는 세션 시작 때 한 번 만들고 고정 (지침 파일·repo map도 시작 때 한 번), 메시지는 덧붙이기만. v06의 압축은 캐시를 깨므로 가끔만 일어나게 한다.
- `cost_limit`: 무한 루프에 빠진 에이전트가 지갑을 비우지 않게.

### 5. 프로젝트 지침 파일
저장소 루트 → 현재 폴더까지 내려오며 `AGENTS.md`(없으면 `CLAUDE.md`, `GEMINI.md`)를 모아 system prompt에 넣는다. "테스트는 `make test`로", "새 의존성 금지" 같은 규칙을 사람이 한 번 적어두면 된다.

## 오픈소스 대응

| 우리 코드 | 오픈소스 |
|---|---|
| SSE 스트리밍 | Gemini CLI [`core/turn.ts` `Turn.run` L270](https://github.com/google-gemini/gemini-cli/blob/51617c1bcbcb038291125b092501b4342f4e7f77/packages/core/src/core/turn.ts#L270) (스트림 청크 → 이벤트) · OpenHands `sdk/llm/streaming.py`, `_response_stream.py` · Codex `core/src/client.rs` (Responses API SSE) |
| `repair_tool_pairs`, 중단 시 결과 채우기 | Codex [`core/src/context_manager/history.rs` `normalize_history` L966](https://github.com/openai/codex/blob/12cca49c81c8aa6a37a5cc4e39964ee3b70f9458/codex-rs/core/src/context_manager/history.rs#L966) — 누락된 call output에 `"aborted"`를 채움 (테스트 `normalize_adds_missing_output_*`) · Codex `core/src/tasks/mod.rs` `TurnAborted` 처리 · OpenHands `sdk/conversation/cancellation.py` |
| `Session` JSONL | Codex `rollout` 크레이트 (`~/.codex/sessions/.../rollout-*.jsonl`, `core/src/rollout.rs`) · Gemini CLI [`services/chatRecordingService.ts` `ChatRecordingService` L670](https://github.com/google-gemini/gemini-cli/blob/51617c1bcbcb038291125b092501b4342f4e7f77/packages/core/src/services/chatRecordingService.ts#L670) (`session-*.jsonl`) · OpenHands `sdk/conversation/event_store.py` `EventLog` |
| `pricing.py`, `cost_limit` | mini-swe-agent [`models/__init__.py` `GlobalModelStats` L13](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/__init__.py#L13) + `AgentConfig.cost_limit` · OpenHands `sdk/llm/utils/metrics.py` `Metrics.accumulated_cost` (litellm 가격표 사용) |
| 프롬프트 캐싱 | mini-swe-agent `models/utils/cache_control.py` `set_cache_control` L49 (마지막 메시지들에 breakpoint) · SWE-agent `history_processors.py` `CacheControlHistoryProcessor` L261 — 둘 다 **수동 breakpoint** 방식, 우리는 Anthropic의 **자동(top-level) 캐싱** |
| `instructions.py` | Codex [`core/src/agents_md.rs` `load_project_instructions` L58](https://github.com/openai/codex/blob/12cca49c81c8aa6a37a5cc4e39964ee3b70f9458/codex-rs/core/src/agents_md.rs#L58) (루트→cwd로 `AGENTS.md` 수집, `AGENTS.override.md`) · Gemini CLI `tools/memoryTool.ts` `DEFAULT_CONTEXT_FILENAME = 'GEMINI.md'` |
| REPL + 슬래시 명령 | aider `aider/commands.py` (`/add`, `/drop`, `/tokens` …) · Gemini CLI `packages/cli` |

## 연습
1. `/model <spec>` 명령으로 세션 중간에 모델을 바꿔보자. v03의 `raw` 처리 덕에 히스토리가 그대로 넘어가는지 확인하자.
2. `/undo` 명령: 마지막 턴에서 `str_replace_editor`가 바꾼 파일들을 되돌려보자 (Editor.history 활용). aider는 git commit으로 같은 일을 한다(`/undo`).
3. 스트리밍 중 도구 인자(`input_json_delta`)도 실시간으로 보여줘 보자 — 큰 파일을 `create`할 때 체감이 크다.
