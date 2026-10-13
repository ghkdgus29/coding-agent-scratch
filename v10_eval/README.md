# v10 — 평가: 개선이 정말 개선인가?

> 목표: "v04가 v01보다 낫다"를 느낌이 아니라 **숫자로** 확인한다. 에이전트 개발의 마지막 퍼즐은 측정이다.

## 실행

```bash
uv run python eval/harness.py --oracle                                          # 과제 자체 검증 (API 불필요)
uv run python eval/harness.py --agent v10_eval                                  # 현재 버전 평가
uv run python eval/harness.py --agent v01_minimal_loop,v04_editing,v10_eval     # 버전 비교 ★
uv run python eval/harness.py --agent v10_eval --tasks parse_duration --repeat 3
uv run python eval/harness.py --agent v10_eval --model openai:gpt-5             # 모델 비교
uv run python -m unittest discover tests
```
결과는 `eval/results/<시각>.jsonl`, 각 실행의 에이전트 로그는 `eval/results/logs/`.

> 💸 실제 모델을 호출하므로 비용이 든다. 6개 과제 × 버전 수 × repeat 만큼 에이전트가 돈다. 처음엔 `--tasks` 하나로 시작하자.

## v09 → v10 변경점

| 파일 | 변경 |
|---|---|
| `eval/harness.py` | **새로 추가.** 과제 준비 → 에이전트 실행(subprocess) → 숨겨진 테스트로 채점 → 표/JSONL |
| `eval/tasks/*` | **새로 추가.** 과제 6개 (아래) |
| `tests/test_harness.py` | 과제 유효성(oracle), 채점, 가짜 에이전트로 end-to-end |

에이전트 코드는 v09와 같다. v10은 **v00~v09 전부를 평가할 수 있는 도구**다.

## 과제

| 과제 | 유형 | 무엇을 보나 |
|---|---|---|
| `fix_off_by_one` | 버그 수정 (증상만 설명) | 증상 → 원인 추적 |
| `implement_slugify` | 명세(docstring)대로 구현 | 요구사항 꼼꼼히 읽기, 엣지 케이스 |
| `rename_across_files` | 여러 파일 리팩터링 | 검색 도구, 빠짐없는 편집 (v04/v07 효과) |
| `parse_duration` | 파서 버그 + 에러 처리 | 테스트 직접 만들어 검증하는 습관 |
| `config_bool_bug` | 미묘한 버그, 기존 동작 유지 | PASS_TO_PASS (다른 걸 안 망가뜨리기) |
| `add_cli_option` | 기존 CLI에 기능 추가 | 출력 형식 정확히 지키기, 하위 호환 |

## 핵심 개념

### 1. 숨겨진 테스트 + 두 종류의 채점
```
repo/ 복사 → 에이전트 실행 (hidden_tests는 아직 없음) → hidden_tests/ 복사 → 채점
  FAIL_TO_PASS : 새 요구사항 테스트가 통과하는가          → 문제를 풀었나
  PASS_TO_PASS : 원래 있던 테스트가 여전히 통과하는가      → 아무것도 안 망가뜨렸나
  resolved = 둘 다 통과
```
에이전트가 채점 테스트를 보면 테스트에 맞춘 꼼수를 쓸 수 있다. 그래서 끝난 뒤에 넣는다. (SWE-bench와 같은 원리)

### 2. 과제부터 검증하라 (`--oracle`)
과제가 틀리면 점수는 의미가 없다. 각 과제의 `solution/`을 적용해 **"수정 전엔 실패, 수정 후엔 통과"** 를 기계적으로 확인한다. 새 과제를 만들면 반드시 oracle부터 돌리자.

### 3. 블랙박스 실행
하네스는 각 버전의 `main.py`를 **subprocess로** 실행한다 (`TASK --cwd DIR --model M --yes`). 그래서 내부 구조가 완전히 다른 v01과 v10을 같은 잣대로 잴 수 있다. 버전마다 지원 옵션이 달라서 `--help`를 읽어 `--stats-json` 같은 옵션을 켤지 정한다 (`agent_flags`).

### 4. LLM 평가의 함정
- **비결정성**: 같은 과제도 매번 결과가 다르다. `--repeat`로 여러 번 돌려 비율로 보자. 6과제 × 1회의 차이 1개는 잡음일 수 있다.
- **작은 과제셋**: 6개는 "회귀 감지"용이지 순위표용이 아니다. 진짜 비교는 SWE-bench(실제 GitHub 이슈 수백 개) 규모가 필요하다.
- **비용·시간도 지표**: 같은 성공률이면 더 싸고 빠른 쪽이 낫다. 표의 `steps`, `cost`를 같이 보자 (v08부터 `--stats-json` 지원).

## 오픈소스 대응

| 우리 코드 | 오픈소스 |
|---|---|
| FAIL_TO_PASS / PASS_TO_PASS 채점 | [SWE-bench](https://github.com/SWE-bench/SWE-bench) 평가 하네스 (인스턴스별 Docker 이미지에서 숨겨진 테스트 실행) |
| `run_agent` (과제별로 에이전트 실행 → 결과 저장) | mini-swe-agent [`run/benchmarks/swebench.py` `process_instance` L122](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/run/benchmarks/swebench.py#L122) — 인스턴스마다 Docker 환경을 띄워 에이전트를 돌리고 `preds.json`(패치)을 모은다. 채점은 SWE-bench 하네스가 따로 한다 |
| `--repeat`, 통과율 집계 | aider [`benchmark/benchmark.py`](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/benchmark/benchmark.py) — Exercism 문제로 `run_test_real` L679 / `run_unit_tests` L981, `--tries`(실패 시 테스트 출력을 보여주고 재시도) 별 `pass_rate_N` 집계(`summarize_results` L468) |
| oracle 검증 | SWE-bench의 gold patch 검증 (데이터셋 구축 시 정답 패치로 FAIL_TO_PASS가 실제로 뒤집히는지 확인) |

## 해볼 실험
1. `--agent v01_minimal_loop,v03_providers,v04_editing,v07_repo_map,v10_eval` 로 한 번 돌려 버전별 성공률·스텝·비용 변화를 표로 만들어보자. 어느 버전에서 가장 크게 좋아지는가?
2. 같은 버전을 `--effort low`와 `--effort high`로 비교해보자.
3. 과제를 직접 추가해보자: `repo/`, `hidden_tests/`, `solution/`, `task.json` → `--oracle`로 검증.
4. 실패한 실행의 `eval/results/logs/*.log`를 읽고, 에이전트가 **왜** 실패했는지 분류해보자 (탐색 실패? 편집 실패? 검증 안 함?). 이게 다음 개선의 출발점이다.
