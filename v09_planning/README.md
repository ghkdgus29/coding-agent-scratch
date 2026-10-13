# v09 — 계획과 분업: Todo · 서브 에이전트 · 계획 모드

> 목표: 긴 작업에서 에이전트가 길을 잃지 않게 한다. (1) 스스로 할 일을 적고 관리하고, (2) 탐색 같은 잡일은 별도 에이전트에게 맡겨 자기 컨텍스트를 깨끗이 유지하고, (3) 큰 변경 전에 먼저 계획을 사람과 합의한다.

## 실행

```bash
uv run python main.py --cwd ~/proj                       # REPL에서 /plan, /todos
uv run python main.py "인증 모듈을 리팩터링할 계획을 세워줘" --cwd ~/proj --plan   # 계획만 받기
uv run python -m unittest discover tests
```
REPL 흐름 예: `/plan` → "결제 모듈에 환불 기능 추가" → (계획 검토) → `/plan` (해제) → "좋아, 진행해"

## v08 → v09 변경점

| 파일 | 변경 |
|---|---|
| `planning.py` | **새로 추가.** `TodoList` + `todo_write` 도구, `task` 도구(서브 에이전트), `read_only_registry` |
| `agent.py` | 도구 목록 = 기본 도구 + `todo_write` + `task`. `spawn_subagent()`, 계획 모드(`set_plan_mode`), 미완료 todo 상기(한 턴에 한 번) |
| `safety.py` | `Guard.read_only` — LOW(읽기) 외 모든 행동 거부 |
| `tools.py` | `ToolRegistry.add` |
| `ui.py` | `SubagentUI` — 서브 에이전트의 도구 호출을 들여써서 표시 |
| `main.py` | `--plan`, `/plan`, `/todos` |

## 핵심 개념

### 1. Todo: 모델의 외부 메모장
```
todo_write([{content: "원인 파악", status: completed},
            {content: "수정",     status: in_progress},   ← 동시에 하나만
            {content: "테스트",   status: pending}])
```
- **전체 교체** 방식: 부분 수정 API보다 단순하고, 매번 모델이 전체 상태를 다시 생각하게 된다.
- 도구 결과로 대화에 남으므로 v06 압축 후에도 최신 목록이 보존된다 (OpenHands 요약 프롬프트도 TASK_TRACKING 섹션을 반드시 유지하라고 지시한다).
- 모델이 미완료 항목을 남긴 채 끝내려 하면 **한 번만** 상기시킨다. 두 번째는 의도적인 종료로 존중.

### 2. 서브 에이전트: 컨텍스트 격리
```
부모: task(description="find auth flow", prompt="...", subagent_type="explore")
  ↳ 서브: 새 대화 [system + <subagent> 안내] + prompt
         grep ×5, view ×8 ... (부모 대화에는 안 남음)
         → 최종 보고 "로그인은 auth/session.py:42 create_session()에서..."
부모: tool_result = 그 보고 한 덩어리
```
- 부모 컨텍스트에는 탐색 잡음 수십 개 대신 **요약 보고 하나**만 들어간다 → 비용·집중력 모두 이득.
- `explore` = 읽기 전용 도구 + `Guard(read_only=True)`: 맡겨도 안전. `general` = 전체 도구 + 부모의 승인 정책 공유.
- 서브 에이전트는 `task` 도구가 없다 → 무한 재귀 방지. 사용자에게 질문할 수 없다 → system에 명시.
- 서브 에이전트의 토큰 사용량은 부모 비용에 합산.
- 트레이드오프: 서브 에이전트는 부모의 맥락을 모른다. 그래서 `prompt`는 **자기완결적**이어야 한다 (도구 설명에 명시).

### 3. 계획 모드: "도구를 빼지 말고 막아라"
계획 모드에서 편집 도구를 목록에서 **제거**하면 직관적이지만, 도구 목록이 바뀌면 프롬프트 캐시가 깨지고(v08) thinking 블록 바인딩도 깨진다(v06). 그래서
- 도구 목록은 **그대로** 두고 (`test_plan_mode_blocks_edits_and_notifies`가 이를 검증)
- `Guard.read_only`로 실행 시점에 거부하고
- 모드 변경은 다음 user 메시지 앞에 `<system-reminder>`를 붙여 **덧붙이는 방식**으로 알린다 (system prompt 수정 X).

## 오픈소스 대응

| 우리 코드 | 오픈소스 |
|---|---|
| `TodoList`, `todo_write` | Gemini CLI [`tools/write-todos.ts` `WriteTodosTool` L77](https://github.com/google-gemini/gemini-cli/blob/51617c1bcbcb038291125b092501b4342f4e7f77/packages/core/src/tools/write-todos.ts#L77) · OpenHands [`tools/task_tracker/definition.py` `TaskTrackerExecutor` L148](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-tools/openhands/tools/task_tracker/definition.py#L148) · Claude Code의 `TodoWrite` 도구(비공개 소스, 동작은 공개 문서 참고) |
| `task` 도구 (description / prompt / subagent_type) | OpenHands [`tools/task/definition.py` L50-59](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-tools/openhands/tools/task/definition.py#L50) — 필드 구성이 거의 같다 · OpenHands `tools/delegate/impl.py` `DelegateExecutor` L33 |
| `spawn_subagent` 실행기 | Gemini CLI [`agents/local-executor.ts` `LocalAgentExecutor` L131](https://github.com/google-gemini/gemini-cli/blob/51617c1bcbcb038291125b092501b4342f4e7f77/packages/core/src/agents/local-executor.ts#L131) · Codex `core/src/codex_delegate.rs` |
| `explore` 서브 에이전트 (읽기 전용) | Gemini CLI [`agents/codebase-investigator.ts` `CodebaseInvestigatorAgent` L51](https://github.com/google-gemini/gemini-cli/blob/51617c1bcbcb038291125b092501b4342f4e7f77/packages/core/src/agents/codebase-investigator.ts#L51) — "Grant access only to read-only tools" (L118) |
| 계획 모드 | Gemini CLI `tools/enter-plan-mode.ts` ("restrict the agent to read-only tools", L110) / `exit-plan-mode.ts` · Codex `collaboration-mode-templates/templates/plan.md` · OpenHands `tools/planning_file_editor` |
| `<system-reminder>` 덧붙이기 | v06/v08의 "히스토리는 append-only" 원칙의 적용 |

## 연습
1. Gemini CLI처럼 `exit_plan_mode(plan)` 도구를 만들어, 모델이 계획을 제출하면 사용자에게 승인(y/n)을 받고 자동으로 계획 모드를 해제해보자.
2. `task`를 병렬로: 한 응답에 `task` 호출이 여러 개면 `concurrent.futures.ThreadPoolExecutor`로 동시에 실행해보자. (UI 출력과 Guard 질문이 섞이는 문제를 어떻게 풀까?)
3. 서브 에이전트에 더 싼 모델을 쓰도록 `--subagent-model` 옵션을 추가해보자.
