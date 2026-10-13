"""v05 CLI.

    uv run python main.py "..." --cwd /tmp/play                         # auto-edit: 편집은 자동, 위험한 명령은 질문
    uv run python main.py "..." --cwd /tmp/play --approval suggest      # 읽기 외 전부 질문
    uv run python main.py "..." --cwd /tmp/play --approval full-auto    # HIGH 위험만 질문
    uv run python main.py "..." --cwd /tmp/play --yes                   # 비대화형 full-auto (HIGH는 자동 거부)
"""

import argparse
import json

from agent import Agent, AgentConfig
from llm import DEFAULT_MODEL, get_llm
from safety import MODES, Guard
from tools import default_tools


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="provider:model")
    ap.add_argument("--base-url", default=None, help="OpenAI 호환 서버 주소 override")
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--step-limit", type=int, default=30)
    ap.add_argument("--time-limit", type=int, default=0, help="전체 실행 시간 제한(초)")
    ap.add_argument("--timeout", type=int, default=60, help="명령 하나당 시간 제한(초)")
    ap.add_argument("--approval", default="auto-edit", choices=MODES)
    ap.add_argument("--yes", action="store_true", help="비대화형 full-auto (HIGH 위험 행동은 자동 거부)")
    args = ap.parse_args()

    guard = Guard(mode="full-auto", ask=None) if args.yes else Guard(mode=args.approval, ask=input)
    llm = get_llm(args.model, effort=args.effort, base_url=args.base_url)
    config = AgentConfig(step_limit=args.step_limit, wall_time_limit=args.time_limit, cwd=args.cwd)
    result = Agent(llm, default_tools(args.cwd, args.timeout), config, guard).run(args.task)
    print("\n=== result ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
