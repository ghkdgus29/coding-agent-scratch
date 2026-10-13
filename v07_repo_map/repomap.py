"""v07: Repo map — 코드베이스의 "지도"를 토큰 예산 안에서 만든다. (aider repomap.py를 표준 라이브러리로)

아이디어
1. 모든 파이썬 파일을 ast로 파싱해서 정의(class/def)와 참조(사용된 이름)를 모은다.  (aider: tree-sitter)
2. "파일 A가 파일 B에 정의된 이름을 쓴다" → A → B 간선. 많이 쓰이는 정의일수록 중요하다.
3. 이 그래프에 PageRank를 돌려 중요한 파일/정의를 찾는다.                          (aider: networkx)
   작업에 언급된 파일·이름에 가중치를 줘서(personalization) 지금 작업과 관련된 쪽으로 치우치게 한다.
4. 순위대로 정의 줄(시그니처)을 나열하되, 토큰 예산에 맞게 이분 탐색으로 개수를 고른다.

결과 예:
    calc/ops.py:
    │class Calculator:
    │    def add(self, a: int, b: int) -> int:
    calc/cli.py:
    │def main(argv=None):
"""

import ast
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from search import is_text_file, iter_files

CODE_SUFFIXES = {".py"}


@dataclass(frozen=True)
class Tag:
    rel: str
    name: str
    line: int  # 1-based


def extract_tags(path: Path, rel: str) -> tuple[list[Tag], list[str]]:
    """(정의 목록, 참조된 이름 목록). 파싱 실패 시 빈 결과."""
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except (SyntaxError, ValueError):
        return [], []
    defs, refs = [], []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.append(Tag(rel, node.name, node.lineno))
        elif isinstance(node, ast.Name):
            refs.append(node.id)
        elif isinstance(node, ast.Attribute):
            refs.append(node.attr)
    return defs, refs


def pagerank(nodes: Iterable[str], edges: dict[tuple[str, str], float], personalization: dict[str, float] | None = None,
             damping: float = 0.85, iterations: int = 50) -> dict[str, float]:
    """가중 PageRank (power iteration). networkx.pagerank를 직접 구현한 것.

    rank(v) = (1-d) * p(v) + d * Σ_u rank(u) * w(u→v) / out_weight(u)
    나가는 간선이 없는 노드(dangling)의 점수는 p 분포대로 다시 뿌린다.
    """
    nodes = list(nodes)
    if not nodes:
        return {}
    if personalization and sum(personalization.values()) > 0:
        total = sum(personalization.get(n, 0.0) for n in nodes)
        p = {n: personalization.get(n, 0.0) / total for n in nodes}
    else:
        p = {n: 1.0 / len(nodes) for n in nodes}
    out_w: dict[str, float] = defaultdict(float)
    incoming: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (u, v), w in edges.items():
        out_w[u] += w
        incoming[v].append((u, w))
    rank = dict(p)
    for _ in range(iterations):
        dangling = sum(rank[n] for n in nodes if out_w[n] == 0)
        new = {}
        for v in nodes:
            s = sum(rank[u] * w / out_w[u] for u, w in incoming[v])
            new[v] = (1 - damping) * p[v] + damping * (s + dangling * p[v])
        rank = new
    return rank


def ident_weight(ident: str, n_definers: int, mentioned: set[str]) -> float:
    """aider의 가중치 규칙 (repomap.py get_ranked_tags)."""
    w = 1.0
    is_snake = "_" in ident and any(c.isalpha() for c in ident)
    is_camel = any(c.isupper() for c in ident) and any(c.islower() for c in ident)
    if ident in mentioned:
        w *= 10           # 작업에서 언급된 이름
    if (is_snake or is_camel) and len(ident) >= 8:
        w *= 10           # 길고 구체적인 이름 (get_user_by_id) > 흔한 이름 (run)
    if ident.startswith("_"):
        w *= 0.1          # private
    if n_definers > 5:
        w *= 0.1          # 너무 많은 곳에서 정의됨 (__init__ 같은)
    return w


class RepoMap:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def collect(self):
        defines: dict[str, set[str]] = defaultdict(set)           # 이름 → 정의한 파일들
        references: dict[str, list[str]] = defaultdict(list)     # 이름 → 참조한 파일들 (중복 = 횟수)
        def_tags: dict[tuple[str, str], list[Tag]] = defaultdict(list)
        all_files: list[str] = []
        for f in iter_files(self.root):
            rel = str(f.relative_to(self.root))
            all_files.append(rel)
            if f.suffix not in CODE_SUFFIXES or not is_text_file(f):
                continue
            defs, refs = extract_tags(f, rel)
            for t in defs:
                defines[t.name].add(rel)
                def_tags[(rel, t.name)].append(t)
            for r in refs:
                references[r].append(rel)
        return defines, references, def_tags, all_files

    def ranked_tags(self, mentioned_files: set[str] = frozenset(), mentioned_idents: set[str] = frozenset()):
        defines, references, def_tags, all_files = self.collect()
        edges: dict[tuple[str, str], float] = defaultdict(float)
        edge_idents: dict[tuple[str, str], Counter] = defaultdict(Counter)
        for ident, definers in defines.items():
            mul = ident_weight(ident, len(definers), mentioned_idents)
            for referencer, n_refs in Counter(references.get(ident, [])).items():
                for definer in definers:  # 자기 파일 안의 참조도 간선으로 센다 (aider도 동일)
                    w = mul * math.sqrt(n_refs)   # 100번 참조가 1번의 100배 중요하진 않다
                    if referencer in mentioned_files:
                        w *= 50                   # 지금 다루는 파일이 쓰는 것들 (aider: chat 파일 ×50)
                    edges[(referencer, definer)] += w
                    edge_idents[(referencer, definer)][ident] += w
            if ident not in references:  # 아무도 안 쓰는 정의도 최소한의 자기 간선
                for definer in definers:
                    edges[(definer, definer)] += 0.1 * mul
                    edge_idents[(definer, definer)][ident] += 0.1 * mul

        nodes = set(all_files) | {u for u, _ in edges} | {v for _, v in edges}
        personalization = {f: 1.0 for f in mentioned_files if f in nodes}
        for ident in mentioned_idents:  # 언급된 이름을 정의한 파일도 출발점으로
            for f in defines.get(ident, ()):
                personalization[f] = personalization.get(f, 0) + 1.0
        rank = pagerank(nodes, edges, personalization or None)

        # 파일 점수를 그 파일로 들어오는 간선의 "이름"들에 나눠준다 → (파일, 이름) 단위 순위
        out_w: dict[str, float] = defaultdict(float)
        for (u, _), w in edges.items():
            out_w[u] += w
        def_rank: dict[tuple[str, str], float] = defaultdict(float)
        for (u, v), idents in edge_idents.items():
            for ident, w in idents.items():
                def_rank[(v, ident)] += rank[u] * w / out_w[u]
        ordered = sorted(def_rank.items(), key=lambda kv: kv[1], reverse=True)
        tags = [t for (key, _) in ordered for t in def_tags.get(key, [])]
        ranked_files = sorted(nodes, key=lambda f: rank.get(f, 0), reverse=True)
        return tags, ranked_files

    def render(self, tags: list[Tag], other_files: list[str] = ()) -> str:
        by_file: dict[str, list[Tag]] = defaultdict(list)
        order: list[str] = []
        for t in tags:
            if t.rel not in by_file:
                order.append(t.rel)
            by_file[t.rel].append(t)
        out = []
        for rel in order:
            lines = (self.root / rel).read_text(errors="replace").split("\n")
            out.append(f"{rel}:")
            for t in sorted(set(by_file[rel]), key=lambda t: t.line):
                src = lines[t.line - 1].rstrip() if t.line - 1 < len(lines) else t.name
                out.append(f"│{src[:200]}")
        shown = set(order)
        out.extend(f for f in other_files if f not in shown)
        return "\n".join(out)

    def get_map(self, max_tokens: int = 2048, mentioned_files: set[str] = frozenset(),
                mentioned_idents: set[str] = frozenset()) -> str:
        tags, ranked_files = self.ranked_tags(set(mentioned_files), set(mentioned_idents))
        files_without_tags = [f for f in ranked_files if f not in {t.rel for t in tags}]
        tokens = lambda s: len(s) // 4

        # 이분 탐색: 예산 안에 들어가는 가장 많은 태그 수 (aider get_ranked_tags_map_uncached)
        lo, hi, best = 0, len(tags), ""
        while lo <= hi:
            mid = (lo + hi) // 2
            text = self.render(tags[:mid])
            if tokens(text) <= max_tokens:
                best, lo = text, mid + 1
            else:
                hi = mid - 1
        # 남은 예산으로 태그 없는 파일 이름도 나열
        extra = []
        for f in files_without_tags:
            if tokens(best + "\n".join(extra + [f])) > max_tokens:
                break
            extra.append(f)
        return "\n".join(x for x in [best, "\n".join(extra)] if x)


def mentions(text: str, root: Path) -> tuple[set[str], set[str]]:
    """작업 문장에서 언급된 파일 경로와 식별자를 뽑는다."""
    words = set(re.findall(r"[A-Za-z_][\w./-]*", text))
    files = {w for w in words if ("/" in w or "." in w) and (root / w).is_file()}
    idents = {w for w in re.findall(r"[A-Za-z_]\w{2,}", text)}
    return files, idents
