"""v01 CLI.

    uv run python main.py "hello.py 를 만들고 실행해줘" --cwd /tmp/play
    uv run python main.py "..." --model openai:gpt-5 --yes
"""

import argparse
import json

import llm
from agent import Agent, AgentConfig


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--model", default="anthropic:claude-opus-5-5", help="provider:model")
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--step-limit", type=int, default=30)
    ap.add_argument("--yes", action="store_true", help="명령 실행 전 확인을 건너뜀 (주의!)")
    args = ap.parse_args()

    agent = Agent(llm.make_llm(args.model), AgentConfig(step_limit=args.step_limit, cwd=args.cwd, confirm=not args.yes))
    result = agent.run(args.task)
    print("\n=== result ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
