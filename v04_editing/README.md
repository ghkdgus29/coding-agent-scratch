# v04 — 파일 편집 전략

> 목표: "파일 전체 덮어쓰기"를 **정밀한 문자열 치환 편집**으로 바꾸고, 편집이 실패할 때 모델이 스스로 고칠 수 있도록 **좋은 에러 메시지**를 준다. 코딩 에이전트 품질에 가장 큰 영향을 주는 부분 중 하나다.

## 실행

```bash
uv run python main.py "calc.py의 divide 함수가 0으로 나눌 때 ValueError를 내도록 고쳐줘" --cwd /tmp/play
uv run python -m unittest discover tests
```

## v03 → v04 변경점

| 파일 | 변경 |
|---|---|
| `editor.py` | **새로 추가.** `str_replace_editor` 도구: `view` / `create` / `str_replace` / `insert` / `undo_edit` |
| `tools.py` | `read_file`·`write_file` 제거 → `bash` + `str_replace_editor` 두 개로. 확인(confirm)은 파일을 바꾸는 명령에만 |
| `agent.py` | system prompt에 "str_replace_editor로 집중된 편집" 지시 |

## 핵심 개념

### 1. 왜 치환(str_replace)인가
| 방식 | 출력량 | 위험 | 사용처 |
|---|---|---|---|
| 전체 덮어쓰기 (`write_file`) | 파일 전체 | 다른 줄을 실수로 바꿈, 큰 파일에서 잘림 | aider `whole` 포맷, v02~v03 |
| **old → new 치환** | 바뀌는 부분 + 약간의 문맥 | old가 정확히 하나여야 함 | SWE-agent, OpenHands, Claude Code, Gemini CLI, aider `diff` 포맷 |
| 패치(diff) 포맷 | hunk 단위 | 포맷 오류 | Codex `apply_patch`, aider `udiff` |
| 줄 번호 기반 | 작음 | 줄 번호가 편집마다 바뀜 | 초기 SWE-agent `edit 10:20` |

### 2. 실패를 정확히, 구체적으로 알려준다
`str_replace`는 다음 경우 **파일을 건드리지 않고** 이유와 해결책을 돌려준다:

| 상황 | 메시지 |
|---|---|
| old_str 0회 일치 | "did not appear verbatim" + **가장 비슷한 실제 줄(줄 번호 포함)** ← aider의 "Did you mean" |
| new_str이 이미 파일에 있음 | "이미 적용된 편집 아닌가?" ← aider |
| old_str 2회 이상 | "appears N times (lines [1, 5])" — 문맥을 더 포함하라 |
| old == new | 아무것도 안 바뀜 |
| 편집 후 파이썬 구문 오류 | **편집을 거부**(원래 파일 유지) + 오류 줄 ← SWE-agent lint 후 revert |

성공 시에도 편집 위치 주변 ±4줄을 **줄 번호와 함께** 보여줘서 모델이 결과를 바로 검증하게 한다.

### 3. 그 외 디테일
- `view`는 `cat -n` 스타일 줄 번호 + `view_range`. 너무 길면 16,000자에서 자르고 "grep -n으로 찾아서 범위를 지정하라"고 안내.
- `create`는 **기존 파일 덮어쓰기를 거부** → 기존 파일은 반드시 `str_replace`로 고치도록 유도.
- `undo_edit`: 파일별 이전 내용 스택.
- `\r\n` 파일: 모델은 `\n`으로 쓰므로 맞춰서 치환하고 줄바꿈을 보존 (`newline=""`).
- 파이썬 구문 검사는 flake8 대신 표준 라이브러리 `ast.parse` — **원래 문법이 맞던 파일이 깨질 때만** 거부한다 (이미 깨진 파일을 고치는 중일 수 있으므로).

## 오픈소스 대응

| 우리 코드 | 오픈소스 |
|---|---|
| `Editor` 전체 (명령 구성, 메시지 문구) | SWE-agent [`tools/edit_anthropic/bin/str_replace_editor`](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/tools/edit_anthropic/bin/str_replace_editor) — `view` L438, `str_replace` L516, `insert` L596, `undo_edit` L634, `MAX_RESPONSE_LEN=16000` L28, `SNIPPET_LINES=4` L35, create 덮어쓰기 거부 L422. Anthropic의 text editor tool 스펙을 구현한 것 |
| 0회/다회 일치 에러 | 위 파일 `str_replace` L524-534 ("did not appear verbatim", "Multiple occurrences ... in lines") |
| `find_similar_lines` + "already applied" 힌트 | aider [`coders/editblock_coder.py` `find_similar_lines` L602](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/coders/editblock_coder.py#L602), 에러 메시지 조립은 `apply_edits` L41-125 ("Did you mean to match some of these actual lines", "The REPLACE lines are already in") |
| 구문 오류 시 revert | SWE-agent [`tools/windowed_edit_linting/bin/edit` L94-118](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/tools/windowed_edit_linting/bin/edit#L94) — flake8 전/후 비교 → 새 오류면 `undo_edit()` |
| (같은 개념, 다른 구현) | OpenHands [`tools/file_editor/editor.py`](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-tools/openhands/tools/file_editor/editor.py) `str_replace` L178 / `undo_edit` L671 |
| (더 나아간 방식) 단계적 느슨한 매칭 | Codex [`apply-patch/src/seek_sequence.rs`](https://github.com/openai/codex/blob/12cca49c81c8aa6a37a5cc4e39964ee3b70f9458/codex-rs/apply-patch/src/seek_sequence.rs#L12): 정확히 → 오른쪽 공백 무시 → 양쪽 공백 무시 순서로 시도 · Gemini CLI [`tools/edit.ts` `calculateReplacement` L303](https://github.com/google-gemini/gemini-cli/blob/51617c1bcbcb038291125b092501b4342f4e7f77/packages/core/src/tools/edit.ts#L303): exact → flexible 전략, 그래도 실패하면 LLM에게 편집을 고쳐달라고 요청 (`FixLLMEditWithInstruction`) |
| (다른 포맷) | aider `editblock_coder.py` — 도구 호출 없이 텍스트 `<<<<<<< SEARCH / ======= / >>>>>>> REPLACE` 블록 (v01처럼 텍스트 파싱) · Codex `apply_patch` (`*** Begin Patch` 포맷, `apply-patch/src/parser.rs`) |

## 한계 (→ 다음 버전)
- 경로 제한이 없다: `path="/etc/hosts"`도 편집 가능하다. bash는 무엇이든 실행한다 → **v05 안전장치**

## 연습
1. Codex처럼 "정확 일치 실패 시 줄 끝 공백 무시 매칭"을 단계적으로 시도하는 fallback을 `str_replace`에 추가해보자. (어떤 경우 위험할까?)
2. aider의 SEARCH/REPLACE 텍스트 블록 파서를 만들어서, tool calling을 못 하는 모델용 편집 경로를 추가해보자.
