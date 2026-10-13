# v06 — 컨텍스트 관리

> 목표: 대화가 길어져도 에이전트가 죽지 않고, 비용이 폭주하지 않게 한다. "무엇을 기억하고 무엇을 잊을지"를 설계한다.

## 실행

```bash
uv run python main.py "..." --cwd /tmp/play                                       # 기본: 150k 토큰에서 압축
uv run python main.py "..." --cwd /tmp/play --context-limit 30000 --max-output-chars 5000   # 압축을 일찍 관찰해보기
uv run python -m unittest discover tests
```

## v05 → v06 변경점

| 파일 | 변경 |
|---|---|
| `context.py` | **새로 추가.** `truncate_middle`, `mask_observations`, `Condenser`(마스킹→요약), `strip_provider_raw`, `LoopDetector` |
| `agent.py` | 도구 출력 자르기 + 전문 저장, `query()`에서 압축 판단, 컨텍스트 초과 에러 복구, 반복 경고 |
| `llm/base.py` | `Usage` 의미 정규화 + `prompt_tokens` (= 실제 요청 입력 크기) |
| `llm/openai.py` | OpenAI `prompt_tokens`에서 캐시 적중분을 빼서 Anthropic과 의미를 맞춤 |

## 핵심 개념

### 왜 문제인가
매 턴 전체 대화를 다시 보낸다(v00). 턴 k의 입력 ≈ k × (턴당 크기) → 총 입력 토큰은 **턴 수의 제곱**에 비례한다. 게다가 `cat huge.log` 한 번이면 10만 토큰이 영구히 대화에 남는다.

### 3단계 방어
```
① 입구에서 자르기   도구 출력 > 30,000자 → 앞 15k + "...N자 생략, 전문은 .agent/outputs/bash_3.txt" + 뒤 15k
                      (에러는 보통 끝에, 맥락은 앞에 있다. 전문이 필요하면 모델이 파일을 grep/view 하면 된다)

② 관찰 가리기       대화 > context_limit → 최근 5개 외의 도구 결과를 "[Old output of bash omitted (812 lines)]"로
                      싸고 정보 손실이 적다: 모델의 "행동/추론"은 남고 오래된 "출력"만 사라진다

③ 요약 압축         ②로도 목표(limit의 50%)까지 안 줄면 →
                      [첫 user 메시지(작업)] + [LLM이 쓴 요약] + [최근 몇 턴 원본]
```

### 압축할 때 지켜야 할 불변식
1. **도구 호출과 결과 쌍을 쪼개지 않는다.** 꼬리를 `tool` 메시지에서 시작하면 짝 없는 `tool_result`가 생겨 API가 400을 낸다. → `tail_start()`는 반드시 assistant 메시지에서 시작 (`test_summary_keeps_task_and_tail_pairs`)
2. **원래 작업(첫 메시지)은 절대 버리지 않는다.**
3. **요약 요청은 도구 없이, 대화를 텍스트로 렌더링해서.** 도구 쌍 문제를 원천 차단.
4. **히스토리를 고쳤으면 thinking 블록을 모두 버린다** (`strip_provider_raw`) — 아래 참고.

### ⚠️ 히스토리 편집의 비용: 캐시와 thinking 블록
- **프롬프트 캐시**: provider는 "이전 요청과 앞부분이 같은" 입력을 싸게(Anthropic 기준 약 1/10~1/20) 처리한다. 과거 메시지를 하나라도 바꾸면 그 지점부터 캐시가 깨진다. 그래서 압축은 **가끔, 한 번에 많이** 하는 게 낫다 (매 턴 조금씩 가리면 매 턴 캐시 미스).
- **thinking 블록 바인딩**: 최신 Claude 모델의 thinking 블록 서명은 "그 블록 이전의 system·tools·메시지 전체"에 묶여 있다. 앞부분을 바꾸고 뒤쪽 thinking 블록을 다시 보내면 검증에 실패한다(최근 생성된 계정은 400 에러). 그래서 압축 후에는 `raw`를 전부 버려서 **thinking 블록을 하나도 다시 보내지 않는다** — 검증할 대상 자체가 없게.
- 대안: Anthropic API는 **서버 측 compaction / context editing**(오래된 tool result를 서버가 지움)을 제공한다. 서버가 처리하므로 위 두 문제가 없다. 직접 구현해보는 게 이 버전의 목적이라 클라이언트 측으로 만들었다.

### 반복 감지
같은 도구를 같은 인자로 4번 연속 부르면 "루프에 빠진 것 같다, 접근을 바꿔라"를 넣는다. 압축으로 과거를 잊은 모델이 같은 시도를 반복하는 일이 실제로 흔하다.

## 오픈소스 대응

| 우리 코드 | 오픈소스 |
|---|---|
| `truncate_middle` (+전문 파일 저장, 줄 번호 안내) | OpenHands SDK [`sdk/utils/truncate.py` `maybe_truncate` L50](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-sdk/openhands/sdk/utils/truncate.py#L50) (head/tail + `save_dir`), 한도 [`tools/terminal/constants.py` `MAX_CMD_OUTPUT_SIZE=30000` L18](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-tools/openhands/tools/terminal/constants.py#L18) · mini-swe-agent `config/default.yaml` `observation_template` (10,000자 넘으면 head 5000 + tail 5000 + 경고) |
| `mask_observations` | SWE-agent [`sweagent/agent/history_processors.py` `LastNObservations` L85](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/sweagent/agent/history_processors.py#L85) — "Old environment output: (n lines omitted)", docstring에 **캐시가 깨진다**는 경고와 `polling` 옵션 · Gemini CLI [`context/chatCompressionService.ts` `collapseOlderFunctionResponses` L131](https://github.com/google-gemini/gemini-cli/blob/51617c1bcbcb038291125b092501b4342f4e7f77/packages/core/src/context/chatCompressionService.ts#L131) |
| `Condenser.summarize`, `SUMMARY_SYSTEM` | OpenHands SDK [`context/condenser/llm_summarizing_condenser.py` `LLMSummarizingCondenser` L54](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py#L54) (`keep_first=2`, `max_size=240`), 프롬프트 `condenser/prompts/summarizing_system.j2` (USER_CONTEXT / COMPLETED / PENDING / CODE_STATE ... 섹션을 거의 그대로 가져옴) |
| `tail_start` (도구 쌍 보존) | OpenHands `llm_summarizing_condenser.py` `_get_forgotten_events` L278 + `context/view/manipulation_indices.py` — "잘라도 되는 경계"만 골라 자른다 · Gemini CLI `chatCompressionService.ts` `findCompressSplitPoint` L282 |
| `trigger_tokens`, `target_ratio=0.5` | Gemini CLI `chatCompressionService.ts` `DEFAULT_COMPRESSION_TOKEN_THRESHOLD = 0.5` (L45, 창의 50%에서 압축), `COMPRESSION_PRESERVE_THRESHOLD = 0.3` (L269, 최근 30% 보존) |
| 컨텍스트 초과 에러 → 강제 압축 | Codex `core/src/compact.rs` (자동 compaction), OpenHands `Agent._log_context_window_exceeded_warning` + condenser의 `handles_condensation_requests` |
| `LoopDetector` | Gemini CLI [`services/loopDetectionService.ts` `TOOL_CALL_LOOP_THRESHOLD = 5` L29](https://github.com/google-gemini/gemini-cli/blob/51617c1bcbcb038291125b092501b4342f4e7f77/packages/core/src/services/loopDetectionService.ts#L29) (같은 도구 호출 5회 / 같은 내용 10회 반복 감지) · OpenHands [`sdk/conversation/stuck_detector.py` `StuckDetector.is_stuck` L104](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-sdk/openhands/sdk/conversation/stuck_detector.py#L104) |
| `Usage.prompt_tokens` 정규화 | OpenHands `sdk/llm/utils/metrics.py` (`Metrics`, cache read/write 토큰 별도 추적) |

## 한계 (→ 다음 버전)
- 에이전트가 낯선 코드베이스에서 `ls`, `cat`, `grep`을 수십 번 반복하며 구조를 파악한다. 비싸고 느리다 → **v07: 검색 도구와 repo map**

## 연습
1. `--context-limit 20000`으로 실제 모델을 돌려 `[context]` 로그가 언제 찍히는지, 요약 뒤 모델이 작업을 제대로 이어가는지 관찰해보자.
2. SWE-agent의 `polling` 아이디어(마스킹 경계를 N스텝마다만 갱신 → 캐시 보존)를 `mask_observations`에 구현해보자.
