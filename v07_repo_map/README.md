# v07 — 코드베이스 이해: 검색 도구 + Repo Map

> 목표: 낯선 저장소에서 에이전트가 `ls`→`cat`→`ls`→`cat`을 수십 번 반복하지 않도록, **처음부터 지도를 주고**, 빠르고 안전한 **검색 도구**를 준다.

## 실행

```bash
uv run python main.py "checkout에 할인 기능을 추가해줘" --cwd ~/some-project
uv run python main.py "..." --cwd ~/some-project --repo-map-tokens 4096    # 지도를 더 크게
uv run python -c "from repomap import RepoMap; print(RepoMap('.').get_map(1000))"   # 이 폴더의 지도 보기
uv run python -m unittest discover tests
```

## v06 → v07 변경점

| 파일 | 변경 |
|---|---|
| `search.py` | **새로 추가.** `grep`(정규식), `glob`(최근 수정순) 도구. `.git`·`node_modules`·`.gitignore` 자동 제외, 결과 수 제한, 바이너리 건너뜀 |
| `repomap.py` | **새로 추가.** `ast` 파싱 → 정의/참조 그래프 → **PageRank** → 토큰 예산 안에서 이분 탐색 렌더링 |
| `tools.py` | `repo_map` 도구 추가, 도구 5개: `bash`, `str_replace_editor`, `grep`, `glob`, `repo_map` |
| `agent.py` | 첫 메시지 = `<repo_map>...</repo_map>` + `<task>...</task>`. 작업 문장에서 언급된 파일/이름으로 지도를 치우치게 함 |

## 핵심 개념

### 1. 왜 전용 검색 도구인가 (bash `grep`이 있는데도)
- 잡음 제거 기본값 (`node_modules`에서 수천 줄 매치 → 컨텍스트 폭발 방지)
- 결과 수 상한 + "패턴을 좁혀라" 안내
- 읽기 전용 → `Review(risk=LOW)` → **승인 없이** 실행 (v05의 Guard와 맞물림)
- macOS/Linux 옵션 차이 없음

### 2. Repo Map: "무엇이 중요한가"를 그래프로
```
① 정의/참조 수집 (ast)          models.py 정의: Product, price_with_tax
                               cart.py   참조: Product, price_with_tax   → cart.py ──▶ models.py
                               api.py    참조: cart_total, Product       → api.py  ──▶ cart.py, models.py
② 간선 가중치 (aider 규칙)       길고 구체적인 이름 ×10 · 작업에 언급된 이름 ×10 · _private ×0.1
                               5곳 넘게 정의된 흔한 이름 ×0.1 · 참조 횟수는 √ (100번 ≠ 100배)
③ PageRank                     많이 "참조당하는" 파일이 높은 점수. 언급된 파일/이름에서 출발(personalization)
④ 파일 점수 → 정의 점수          들어오는 간선의 이름별 가중치 비율로 나눠줌
⑤ 예산 맞추기 (이분 탐색)        상위 k개 정의를 렌더링했을 때 ≤ max_tokens 인 최대 k
```
PageRank는 `pagerank()`에 30줄로 직접 구현했다 (power iteration). networkx가 하는 일이 이것이다.

### 3. 지도는 첫 메시지에, 한 번만
지도를 system prompt에 매 턴 새로 계산해 넣으면 앞부분이 바뀌어 **프롬프트 캐시가 깨진다**(v06 참고). 그래서 시작할 때 한 번 계산해 첫 user 메시지에 넣는다. v06의 Condenser는 첫 메시지를 항상 보존하므로 압축 후에도 지도가 남는다. 작업 중 갱신이 필요하면 모델이 `repo_map(focus_files=[...])` 도구로 다시 요청한다.

## 오픈소스 대응

| 우리 코드 | 오픈소스 |
|---|---|
| `RepoMap` 전체 구조 | aider [`aider/repomap.py` `RepoMap` L42](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/repomap.py#L42) |
| `extract_tags` (ast) | aider `get_tags_raw` L279 — **tree-sitter** 쿼리(`queries/*-tags.scm`)로 다국어 정의/참조 추출. 우리는 파이썬만 `ast`로 |
| `ranked_tags` 가중치 규칙 + 자기 참조 포함 | aider [`get_ranked_tags` L365](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/repomap.py#L365) — `mul *= 10`(언급/긴 이름), `0.1`(private, 흔한 정의), chat 파일 `×50`, `math.sqrt(num_refs)` (L488-511) |
| `pagerank` | aider는 `networkx.pagerank(G, weight="weight", personalization=...)` 사용 |
| `get_map` 이분 탐색 | aider `get_ranked_tags_map_uncached` L629 (`while lower_bound <= upper_bound`) |
| `render` (`│` 접두 시그니처 줄) | aider `to_tree` L748 / `render_tree` L710 (tree-sitter `TreeContext`로 관심 줄만 보여줌) |
| `mentions` | aider [`coders/base_coder.py` `check_for_file_mentions` L1761](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/coders/base_coder.py#L1761), 지도 주입은 `get_repo_map` L709 |
| `grep` / `glob` 도구 | OpenHands [`openhands-tools/tools/grep`](https://github.com/OpenHands/software-agent-sdk/tree/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-tools/openhands/tools/grep), [`tools/glob/impl.py`](https://github.com/OpenHands/software-agent-sdk/blob/d97eb76f60829a4250dd313f5a9d4d03e2951de8/openhands-tools/openhands/tools/glob/impl.py) (ripgrep 우선, 없으면 파이썬 fallback, mtime 정렬) · Gemini CLI `tools/grep.ts`, `tools/ripGrep.ts`, [`tools/glob.ts`](https://github.com/google-gemini/gemini-cli/blob/51617c1bcbcb038291125b092501b4342f4e7f77/packages/core/src/tools/glob.ts#L45) (최근 파일 먼저) · SWE-agent `tools/search/bin/{find_file,search_dir,search_file}` |
| `.gitignore` 존중 | Gemini CLI `services/fileDiscoveryService.ts` |
| (비슷한 아이디어) 함수 본문 생략 보기 | SWE-agent [`tools/filemap/bin/filemap`](https://github.com/SWE-agent/SWE-agent/blob/3ea751c087f32b16e039a2233dd6eefecef325d5/tools/filemap/bin/filemap) — tree-sitter로 긴 함수 본문을 접은 파일 개요 |

## 한계 (→ 다음 버전)
- 한 번 실행하고 끝나는 CLI다. 응답이 다 올 때까지 아무것도 안 보이고, Ctrl+C 하면 작업이 날아가고, 비용을 모른다 → **v08 실사용 UX**

## 연습
1. `extract_tags`를 확장해 `.js`/`.ts` 파일에서 정규식으로 `function foo(`/`class Foo`를 뽑아보자. (tree-sitter가 왜 필요한지 느껴보자)
2. `get_map`에 결과 캐시(파일 mtime 기반)를 추가해보자 — aider `TAGS_CACHE` 참고.
