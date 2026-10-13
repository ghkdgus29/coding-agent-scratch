# 순수 파이썬으로 만드는 코딩 에이전트 — v00 → v10

LLM API 호출 한 번(v00)에서 시작해, 오픈소스 코딩 에이전트들의 핵심 아이디어를 하나씩 옮겨오며 **계획·위임·평가까지 갖춘 에이전트(v10)** 로 키워가는 학습용 프로젝트.

- **표준 라이브러리만** 사용 (`urllib`, `json`, `subprocess`, `ast`, `difflib`, `shlex`, `unittest` …). 런타임 의존성 0개
- **Python 3.14 + [uv](https://docs.astral.sh/uv/)** 로 관리 (`pyproject.toml`의 `dependencies = []`, `.python-version`)
- **버전 = 폴더 스냅샷**. 각 폴더는 그 시점의 완성된 에이전트이고 단독으로 실행된다
- 각 버전 `README.md`에 **무엇이 바뀌었나 / 왜 / 실제 오픈소스의 어느 파일·함수에 대응되나 (커밋 고정 링크)**
- 모든 버전에 **FakeLLM 기반 테스트** — API 키 없이 동작 검증

## 로드맵

| 버전 | 주제 | 새로 배우는 것 | 주로 참고한 오픈소스 | 코드 | 테스트 |
|---|---|---|---|---:|---:|
| [v00](v00_api_call/) | LLM API 직접 호출 | HTTP+JSON, messages, stateless, 토큰 | (LiteLLM이 하는 일) | 167 | 3 |
| [v01](v01_minimal_loop/) | 최소 에이전트 루프 | query→parse→execute→observe, 예외로 흐름 제어 | **mini-swe-agent** | 374 | 10 |
| [v02](v02_tool_calling/) | Native tool calling | tool schema, tool_use/tool_result, 병렬 호출, 에러도 관찰 | mini-swe-agent v2, OpenHands, Gemini CLI | 456 | 13 |
| [v03](v03_providers/) | Provider 추상화 | 중립 메시지 타입, 포맷 변환, `raw` 보존, 재시도 | mini-swe-agent `models/`, OpenHands `llm/message.py` | 621 | 22 |
| [v04](v04_editing/) | 파일 편집 전략 | str_replace, "did you mean", 구문 오류 시 거부, undo | **SWE-agent** str_replace_editor, **aider** editblock | 857 | 37 |
| [v05](v05_safety/) | 안전장치 | 위험도 분류, 승인 정책, 작업 폴더 격리, 프로세스 그룹 kill | **Codex** exec_policy, OpenHands security | 1132 | 55 |
| [v06](v06_context/) | 컨텍스트 관리 | 출력 자르기, 관찰 마스킹, LLM 요약, 반복 감지, 캐시/thinking 보존 | **OpenHands** condenser, SWE-agent, Gemini CLI | 1379 | 68 |
| [v07](v07_repo_map/) | 코드베이스 이해 | grep/glob 도구, ast 기반 repo map, PageRank | **aider** repomap | 1756 | 82 |
| [v08](v08_ux/) | 실사용 UX | SSE 스트리밍, REPL, Ctrl+C, JSONL 세션, 비용, 프롬프트 캐싱, AGENTS.md | **Codex**, Gemini CLI | 2407 | 96 |
| [v09](v09_planning/) | 계획과 분업 | todo 도구, 서브 에이전트, 계획 모드 | Gemini CLI agents, OpenHands task/delegate | 2643 | 104 |
| [v10](v10_eval/) | 평가 | 숨겨진 테스트, FAIL_TO_PASS/PASS_TO_PASS, oracle 검증, 버전 비교 | SWE-bench, mini-swe-agent, aider benchmark | 2643 | 109 |

(코드 = `tests/`, `eval/` 제외 파이썬 줄 수)

## 빠른 시작

```bash
uv sync                             # Python 3.14 설치(필요 시) + .venv 생성. 설치할 패키지는 없다
export ANTHROPIC_API_KEY=...        # 또는 OPENAI_API_KEY / OPENROUTER_API_KEY

./run_tests.sh                      # 모든 버전 테스트 (API 키 불필요)

mkdir -p /tmp/play
uv run python v01_minimal_loop/main.py "fizzbuzz.py를 만들고 실행해줘" --cwd /tmp/play
uv run python v10_eval/main.py --cwd /tmp/play                       # 대화형 REPL (/help)
uv run python v10_eval/main.py "..." --cwd /tmp/play --model openai:gpt-5
uv run python v10_eval/eval/harness.py --oracle                      # 평가 과제 검증
uv run python v10_eval/eval/harness.py --agent v01_minimal_loop,v10_eval --tasks fix_off_by_one   # 버전 비교 (API 비용 발생)
```
기본 모델은 `anthropic:claude-opus-5-5`. `--model provider:model` 로 `anthropic` / `openai` / `openrouter` / `ollama`(v03+) 선택.

> 버전 폴더 안에서도 `uv run python main.py ...`가 동작한다 (uv가 상위의 `pyproject.toml`을 찾는다).

## 읽는 법

1. 각 버전의 `README.md` → 코드 → 테스트 순서로 읽는다.
2. **이전 버전과의 차이를 직접 본다** — 이게 이 프로젝트의 핵심이다:
   ```bash
   diff -ru -x __pycache__ -x README.md v03_providers v04_editing | less
   ```
3. README의 오픈소스 링크를 열어 "진짜 프로젝트는 같은 문제를 어떻게 풀었나"를 비교한다. 링크는 아래 커밋에 고정되어 있다.
4. 각 README 끝의 **연습**을 해본다.

## 버전을 관통하는 원칙들

| 원칙 | 처음 등장 | 이후 |
|---|---|---|
| **에러도 관찰이다** — 실패를 예외로 죽이지 말고 모델에게 보여줘서 스스로 고치게 | v01 FormatError | v02 `is_error`, v04 편집 실패 힌트, v05 거부 피드백 |
| **모든 tool call에는 짝이 되는 result** | v02 | v06 압축 경계, v08 Ctrl+C·크래시 복구 |
| **provider 원본은 손대지 않고 되돌려준다** (thinking 블록) | v02 | v03 `raw`, v06 히스토리 편집 시 일괄 제거 |
| **히스토리는 덧붙이기만** (프롬프트 캐시, thinking 바인딩) | v06 | v07 repo map은 첫 메시지에 한 번, v08 system 고정, v09 계획 모드를 도구 제거 대신 Guard로 |
| **위험은 분류하고, 승인은 정책으로** | v05 | v09 read-only 서브 에이전트/계획 모드 |
| **측정하라** | v10 | — |

## 참고한 오픈소스 (README 링크 고정 커밋)

| 프로젝트 | 커밋 | 주로 대응되는 버전 |
|---|---|---|
| [SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | `04d809c` | v01, v02, v03, v05, v08, v10 |
| [SWE-agent/SWE-agent](https://github.com/SWE-agent/SWE-agent) | `3ea751c` | v01, v04, v05, v06 |
| [Aider-AI/aider](https://github.com/Aider-AI/aider) | `5dc9490` | v04, v07, v10 |
| [OpenHands/software-agent-sdk](https://github.com/OpenHands/software-agent-sdk) (OpenHands의 파이썬 에이전트 코어) | `d97eb76` | v02, v03, v05, v06, v09 |
| [openai/codex](https://github.com/openai/codex) (Rust) | `12cca49` | v04, v05, v08 |
| [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) (TypeScript) | `51617c1` | v02, v04, v06, v07, v08, v09 |

## 이 프로젝트가 다루지 않는 것 (다음 단계 아이디어)

- **진짜 샌드박스**: Docker / seatbelt / bubblewrap 격리 (v05는 휴리스틱만)
- **MCP**: 외부 도구 서버 연결
- **git 통합**: 편집마다 자동 커밋, `/undo` (aider)
- **다국어 repo map**: tree-sitter (v07은 파이썬 `ast`만)
- **병렬 서브 에이전트**, 서브 에이전트용 저렴한 모델
