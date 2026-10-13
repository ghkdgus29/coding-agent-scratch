"""v08 CLI — 한 번 실행 모드와 대화형(REPL) 모드.

    uv run python main.py --cwd ~/proj                         # 대화형: 여러 작업을 같은 세션에서
    uv run python main.py "테스트 고쳐줘" --cwd ~/proj           # 한 번 실행
    uv run python main.py --cwd ~/proj --resume last           # 마지막 세션 이어서
    uv run python main.py --cwd ~/proj --sessions              # 세션 목록
    uv run python main.py "..." --yes --stats-json out.json    # 비대화형 + 결과 통계 저장 (평가용)
"""

import argparse
import json
import sys

from agent import Agent, AgentConfig
from llm import DEFAULT_MODEL, get_llm
from pricing import parse_price
from safety import MODES, Guard
from session import Session, list_sessions, open_session
from tools import default_tools
from ui import UI

HELP = """명령어
  /help        이 도움말
  /cost        토큰 사용량과 비용
  /compact     지금 대화를 요약해서 압축
  /sessions    저장된 세션 목록
  /exit        종료 (Ctrl+D)
작업 중 Ctrl+C → 현재 턴만 중단 (세션 유지)"""


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", nargs="?", help="없으면 대화형 모드")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="provider:model")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--step-limit", type=int, default=50, help="턴당 최대 LLM 호출 수")
    ap.add_argument("--time-limit", type=int, default=0)
    ap.add_argument("--cost-limit", type=float, default=0.0, help="세션 누적 비용 한도($)")
    ap.add_argument("--price", default=None, help="표에 없는 모델의 가격: 입력,출력[,캐시읽기,캐시쓰기] $/1M")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--context-limit", type=int, default=150_000)
    ap.add_argument("--max-output-chars", type=int, default=30_000)
    ap.add_argument("--repo-map-tokens", type=int, default=2048)
    ap.add_argument("--no-stream", action="store_true")
    ap.add_argument("--approval", default="auto-edit", choices=MODES)
    ap.add_argument("--yes", action="store_true", help="비대화형 full-auto (HIGH 위험 행동은 자동 거부)")
    ap.add_argument("--resume", default=None, metavar="last|ID")
    ap.add_argument("--sessions", action="store_true", help="세션 목록을 보여주고 종료")
    ap.add_argument("--stats-json", default=None, help="결과 통계를 JSON 파일로 저장")
    return ap


def make_agent(args, ui: UI) -> Agent:
    guard = Guard(mode="full-auto", ask=None) if args.yes else Guard(mode=args.approval, ask=ui.ask)
    llm = get_llm(args.model, effort=args.effort, base_url=args.base_url, log=ui.info)
    config = AgentConfig(step_limit=args.step_limit, wall_time_limit=args.time_limit, cost_limit=args.cost_limit,
                         cwd=args.cwd, max_output_chars=args.max_output_chars, context_limit=args.context_limit,
                         repo_map_tokens=args.repo_map_tokens, stream=not args.no_stream)
    price = parse_price(args.price) if args.price else None

    session = None
    if args.resume:
        session = open_session(args.cwd, args.resume)
        if session is None:
            raise SystemExit(f"세션을 찾을 수 없습니다: {args.resume}")
    agent = Agent(llm, default_tools(args.cwd, args.timeout), config, guard, session=session, ui=ui, price=price)
    if session:
        messages, usage, meta = session.load()
        agent.resume(messages, usage)
        ui.info(f"[resumed session {session.id}: {len(messages)} messages, model at start: {meta.get('model')}]")
    else:
        agent.session = Session.create(args.cwd, args.model)
    return agent


def cost_line(agent: Agent) -> str:
    s = agent.stats()
    cost = "$?" if s["cost_usd"] is None else f"${s['cost_usd']:.4f}"
    return (f"[tokens in={s['input_tokens']:,} (cached {s['cache_read_tokens']:,}) out={s['output_tokens']:,} "
            f"· cost {cost} · session {s['session']}]")


def repl(agent: Agent, ui: UI) -> None:
    ui.info(f"coding agent v08 — {agent.llm.model} @ {agent.config.cwd}  (/help)")
    while True:
        try:
            line = ui.ask("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith("/"):
            cmd = line.split()[0]
            if cmd in ("/exit", "/quit"):
                break
            elif cmd == "/help":
                print(HELP)
            elif cmd == "/cost":
                ui.info(cost_line(agent))
            elif cmd == "/compact":
                if agent.messages:
                    agent.set_messages(agent.condenser.condense(agent.messages, agent.system, force_summary=True))
                    agent.last_prompt_tokens = 0
            elif cmd == "/sessions":
                for p in list_sessions(agent.config.cwd)[:20]:
                    print(" ", p.stem)
            else:
                ui.info(f"알 수 없는 명령: {cmd}")
            continue
        result = agent.run_turn(line)
        if result["exit_status"] not in ("Done", "Interrupted"):
            ui.info(f"[{result['exit_status']}] {result['result']}")
        ui.info(cost_line(agent))


def main() -> None:
    args = build_parser().parse_args()
    if args.sessions:
        for p in list_sessions(args.cwd):
            print(p.stem)
        return
    ui = UI()
    agent = make_agent(args, ui)
    if args.task is None:
        if not sys.stdin.isatty():
            raise SystemExit("대화형 모드는 터미널이 필요합니다. 작업을 인자로 주세요.")
        repl(agent, ui)
        return
    result = agent.run_turn(args.task)
    ui.info(cost_line(agent))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.stats_json:
        with open(args.stats_json, "w") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
