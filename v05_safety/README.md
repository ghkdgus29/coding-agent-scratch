# v05 — 안전장치: 위험도 분류 · 승인 정책 · 작업 폴더 격리

> 목표: LLM이 내 컴퓨터에서 명령을 실행한다는 것의 위험을 다룬다. "무엇이 위험한가"와 "언제 사람에게 물어야 하나"를 분리해서 설계한다.

## 실행

```bash
uv run python main.py "..." --cwd /tmp/play                         # 기본 auto-edit
uv run python main.py "..." --cwd /tmp/play --approval suggest      # 읽기 외 모두 질문
uv run python main.py "..." --cwd /tmp/play --approval full-auto    # HIGH만 질문
uv run python main.py "..." --cwd /tmp/play --yes --time-limit 600  # 비대화형 (HIGH는 자동 거부) — 평가/CI용
uv run python -m unittest discover tests
```
승인 질문에는 `y`(허용) / `n`(거부) / `a`(이 세션 동안 같은 명령 이름 항상 허용) / **아무 문장**(거부 + 그 문장을 모델에게 피드백으로 전달)으로 답한다.

## v04 → v05 변경점

| 파일 | 변경 |
|---|---|
| `safety.py` | **새로 추가.** `classify_command()` (LOW/MEDIUM/HIGH), `interactive_block_reason()`, `Guard` (승인 정책) |
| `tools.py` | `Tool.review` 추가 (실행 전 위험도 설명). bash: 대화형 차단·stdin 닫기·페이저 끄기·**프로세스 그룹 kill**. `confirm` 콜백 제거 |
| `editor.py` | **작업 폴더 밖 경로 거부** (`../`, 절대경로, 심볼릭 링크 탈출). `review`: view=LOW, 나머지=MEDIUM |
| `agent.py` | 모든 도구 호출이 `Guard.review()`를 거친다. 거부 → 실행 없이 is_error 결과. `wall_time_limit` |
| `main.py` | `--approval`, `--yes`, `--time-limit`, `--timeout` |

## 핵심 개념

### 1. 위험도와 승인 정책은 별개다
```
             ┌─ classify_command("rm -rf build") → MEDIUM, "recursive force delete"
도구 호출 ──▶│
             └─ Guard(mode).review(kind, risk) → 허용 / 사용자에게 질문 / 거부
```
| | LOW (읽기) | MEDIUM 편집 | MEDIUM 명령 | HIGH |
|---|---|---|---|---|
| `suggest` | 자동 | 질문 | 질문 | 질문 |
| `auto-edit` (기본) | 자동 | 자동 | 질문 | 질문 |
| `full-auto` | 자동 | 자동 | 자동 | 질문 |
| 비대화형 (`--yes`) | 자동 | 자동 | 자동 | **거부** |

**HIGH는 어떤 모드에서도 자동 허용되지 않는다.** 물어볼 사람이 없으면 거부한다. (Codex와 같은 규칙)

### 2. 명령 분류 (휴리스틱)
`shlex`로 토큰화 → `&&`, `||`, `;`, `|`로 조각냄 → 조각별 분류 → **최대값**.
- `sudo`, `env X=1`, `nohup` 같은 래퍼를 벗기고 실제 명령을 본다.
- LOW: `ls`, `cat`, `grep`, `git status/diff/log`, `find`(부작용 없는), `sed -n`
- HIGH: `rm -rf /` `~` 등 넓은 경로, `sudo`, `curl | sh`, `git push --force`, `git reset --hard`, `dd of=`, fork bomb
- 분석 불가(`$(...)`, 파일로 `>` 리다이렉트, 파싱 실패) → 최소 MEDIUM

> ⚠️ 셸은 너무 강력해서 정적 분석은 **우회 가능하다** (`uv run python -c "import shutil; shutil.rmtree('/')"`는 MEDIUM). 진짜 격리는 OS 샌드박스(seatbelt, bubblewrap, Docker)가 해야 한다. Codex·OpenHands·mini-swe-agent 모두 샌드박스/컨테이너를 1차 방어선으로 쓰고, 이런 분류는 2차 방어선(UX)이다.

### 3. 거부도 관찰이다
거부된 도구는 실행되지 않고, `"The user denied this action with feedback: pytest를 써"` 같은 is_error 결과가 된다. 모델은 이걸 보고 접근을 바꾼다. 사람이 에이전트를 **조종**하는 채널이 된다.

### 4. 실행 환경 다듬기
| 문제 | 해결 |
|---|---|
| `git log`가 페이저(less)를 열고 멈춤 | `PAGER=cat GIT_PAGER=cat` |
| `apt install`이 `[Y/n]` 입력을 기다림 | `stdin=DEVNULL` |
| `vim`, `python`(REPL), `tail -f` | 실행 전 차단 + 대안 안내 |
| 타임아웃 후에도 `sh -c`의 자식이 살아남음 (v01~v04의 버그!) | `start_new_session=True` + `os.killpg` — `test_timeout_kills_children` |
| `../../etc/passwd`, 심볼릭 링크 탈출 | `Path.resolve()` 후 `relative_to(root)` 검사 |

## 오픈소스 대응

| 우리 코드 | 오픈소스 |
|---|---|
| 승인 모드 (`suggest`/`auto-edit`/`full-auto`) | Codex [`protocol/src/protocol.rs` `AskForApproval` L1092](https://github.com/openai/codex/blob/12cca49c81c8aa6a37a5cc4e39964ee3b70f9458/codex-rs/protocol/src/protocol.rs#L1092) (`untrusted` / `on-request` / `never` …; 구버전 Codex CLI의 모드 이름이 suggest/auto-edit/full-auto였다) |
| `Guard.review`: HIGH → 질문, 질문 불가 → 거부 | Codex [`core/src/exec_policy.rs` `render_decision_for_unmatched_command_for_platform` L770](https://github.com/openai/codex/blob/12cca49c81c8aa6a37a5cc4e39964ee3b70f9458/codex-rs/core/src/exec_policy.rs#L770) — "dangerous면 `Prompt`, 단 `Never`면 `Forbidden`" 그대로 |
| `a` = 이 명령 항상 허용 | Codex `exec_policy.rs` `derive_requested_execpolicy_amendment_from_prefix_rule` L958 (승인한 명령 prefix를 정책 규칙으로 추가) · mini-swe-agent [`agents/interactive.py` `whitelist_actions` L27](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/interactive.py#L27) |
| `classify_command` (rm -f, sudo 래퍼 재귀) | Codex [`shell-command/src/command_safety/is_dangerous_command.rs`](https://github.com/openai/codex/blob/12cca49c81c8aa6a37a5cc4e39964ee3b70f9458/codex-rs/shell-command/src/command_safety/is_dangerous_command.rs) — `rm_args_include_force_option`, `sudo`/`env` 래퍼를 벗기고 재귀 검사 |
| LOW/MEDIUM/HIGH + 위험도 임계값 | OpenHands SDK [`security/risk.py` `SecurityRisk`](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-sdk/openhands/sdk/security/risk.py#L13), [`security/confirmation_policy.py` `AlwaysConfirm`/`NeverConfirm`/`ConfirmRisky`](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-sdk/openhands/sdk/security/confirmation_policy.py#L27) — OpenHands는 위험도를 **LLM이 직접 판단**하게도 한다(`security/llm_analyzer.py`) |
| 대화형 명령 차단 | SWE-agent [`sweagent/tools/tools.py` `blocklist` L37 / `blocklist_standalone` L51](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/sweagent/tools/tools.py#L37) |
| `run_shell` 프로세스 그룹 kill | mini-swe-agent [`environments/local.py` `_run` L72-89](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/local.py#L72) (`start_new_session` + `os.killpg`) |
| `AGENT_ENV` (PAGER=cat 등) | mini-swe-agent `config/default.yaml`의 `environment.env` |
| 작업 폴더 격리 | Codex `core/src/safety.rs` `assess_patch_safety` L67 (패치가 writable roots 안에만 쓰는지 검사) + OS 샌드박스(`linux-sandbox`, `bwrap`, seatbelt) |
| (참고) 환경변수 정리 | Gemini CLI `services/environmentSanitization.ts` `sanitizeEnvironment` — API 키 등 비밀 변수를 자식 프로세스에 넘기지 않음 |
| 시간/스텝 제한 | mini-swe-agent `agents/default.py` `AgentConfig.step_limit / wall_time_limit_seconds` |

## 한계 (→ 다음 버전)
- 명령 출력이 수만 줄이면? 컨텍스트가 터진다. 긴 작업에선 대화 자체가 한도를 넘는다 → **v06 컨텍스트 관리**

## 연습
1. `uv run python -c "..."` 안의 코드에 `shutil.rmtree`, `os.remove`가 있으면 HIGH로 올리는 규칙을 추가해보자. (이런 규칙이 왜 끝이 없는지 느껴보자)
2. macOS라면 `sandbox-exec`(seatbelt) 프로필로 bash 명령을 작업 폴더 쓰기만 허용하도록 감싸보자. (Codex `core/src/sandboxing` 참고)
