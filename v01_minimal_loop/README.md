# v01 — 최소 에이전트 루프 (bash 하나로 다 한다)

> 목표: "에이전트 = LLM + 루프 + 실행 환경"이라는 핵심 구조를 150줄로 이해한다.

## 실행

```bash
mkdir -p /tmp/play
uv run python main.py "fizzbuzz.py를 만들고 1~15까지 실행해서 결과를 확인해줘" --cwd /tmp/play
uv run python main.py "..." --cwd /tmp/play --yes          # 확인 없이 실행 (주의)
uv run python -m unittest discover tests                    # FakeLLM으로 API 없이 테스트
```

## v00 → v01 변경점

| 파일 | 변경 |
|---|---|
| `llm.py` | `make_llm("provider:model")` 추가 — 에이전트는 provider를 몰라도 되는 `(system, messages) -> text` 함수만 받는다 |
| `agent.py` | **새로 추가.** 에이전트 루프 |
| `fake_llm.py` | **새로 추가.** 정해진 응답을 순서대로 돌려주는 테스트용 LLM |
| `main.py` | CLI (`chat.py` 대체) |

## 핵심 개념

```
run(task)
 └─ while True:
      step():
        query()          LLM 호출 → assistant 메시지 추가
        parse_action()   ```bash ...``` 정규식으로 명령 1개 추출  ──(0개/2개+)──▶ FormatError
        execute()        subprocess로 실행 → returncode + output
        observation      결과를 user 메시지로 추가
      ── NonTerminating 예외 → 에러 메시지를 대화에 넣고 계속
      ── Terminating 예외   → 루프 종료 (Submitted / LimitsExceeded)
```

1. **행동은 텍스트 포맷으로** — v01의 LLM은 텍스트만 낸다. 그래서 "응답에 ```bash 블록을 정확히 하나" 라는 **약속**을 system prompt로 걸고, 정규식으로 파싱한다. 약속을 어기면 `FormatError`를 *대화에 넣어서* 모델이 스스로 고치게 한다. (에러를 대화로 되먹임 하는 패턴은 끝까지 계속 쓰인다.)
2. **관찰(observation)은 user 메시지** — 실행 결과는 `<returncode>`/`<output>` 태그로 감싸 user 역할로 넣는다. 모델 입장에선 "환경이 말해준 것"이다.
3. **종료 조건** — 특수 문자열 `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`을 echo하면 끝. 무한 루프 방지를 위한 `step_limit`.
4. **예외로 흐름 제어** — `step()` 안 어디서든 예외를 던지면 `run()`이 받아서 "계속할지/끝낼지"를 정한다. 루프 코드가 단순해진다.
5. **매 명령은 새 subshell** — `cd`가 유지되지 않는다. 그래서 프롬프트에 "`cd /path && ...`를 쓰라"고 알려준다. (상태 없는 실행 = 단순 + 재현 가능)

## 오픈소스 대응 (거의 1:1)

v01은 **mini-swe-agent**를 표준 라이브러리로 옮긴 것이다. mini-swe-agent는 "100줄짜리 에이전트로 SWE-bench 상당 부분을 푼다"는 걸 보여준 프로젝트다.

| 우리 코드 | mini-swe-agent (@04d809c) |
|---|---|
| `Agent.run` / `step` / `query` | [`agents/default.py` `DefaultAgent.run` L88, `step` L126, `query` L130](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py#L88) |
| `NonTerminating` / `Terminating` 예외 | [`exceptions.py`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/exceptions.py) — `InterruptAgentFlow` 하나를 상속하고, 종료 여부는 `role: "exit"` 메시지로 판단. 우리는 이해하기 쉽게 두 계열로 나눴다 (구버전 mini-swe-agent v1의 방식) |
| `parse_action` + `FORMAT_ERROR_TEMPLATE` | [`models/utils/actions_text.py` `parse_regex_actions`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/utils/actions_text.py#L15) — "Expected exactly 1 action" |
| `SYSTEM_TEMPLATE`, `INSTANCE_TEMPLATE`, `OBSERVATION_TEMPLATE` | [`config/default.yaml`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/config/default.yaml) (jinja 템플릿 → 우리는 `str.format`) |
| `execute` + `_check_finished` | [`environments/local.py` `LocalEnvironment.execute` / `_check_finished`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/local.py#L24) |
| `FakeLLM` | [`models/test_models.py` `DeterministicModel`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/test_models.py#L104) |
| `confirm` (실행 전 y/N) | `agents/interactive.py` 의 `mode: "human" / "confirm" / "yolo"` (L25) |

원조 SWE-agent도 같은 루프 구조다: [`sweagent/agent/agents.py` `DefaultAgent.step` L1235 / `forward` L1006](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/sweagent/agent/agents.py#L1235).

## 한계 (→ 다음 버전들이 해결)

- 정규식 파싱이 깨지기 쉽다. 코드블록 안에 ``` 가 들어가면? → **v02 native tool calling**
- 파일 편집을 `sed`/heredoc으로 해야 해서 실수가 잦다 → **v04 편집 도구**
- 출력이 길면 컨텍스트가 폭발한다 → **v06**
- 타임아웃 시 `shell=True`의 자식 프로세스가 살아남을 수 있다 → **v05** (프로세스 그룹 kill)

## 연습

1. `ACTION_REGEX`를 ```` ```bash ```` 대신 ```` ```sh ````도 허용하도록 바꿔보고 테스트를 추가해보자.
2. `test_creates_file_and_submits`처럼 FakeLLM 응답을 짜서 "파일이 없을 때 에러 → 생성 → 재실행" 시나리오 테스트를 만들어보자.
