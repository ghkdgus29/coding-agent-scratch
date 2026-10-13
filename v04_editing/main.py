"""v03 CLI.

    uv run python main.py "..." --cwd /tmp/play                                # Anthropic (기본)
    uv run python main.py "..." --model openai:gpt-5
    uv run python main.py "..." --model openrouter:<vendor>/<model>
    uv run python main.py "..." --model ollama:qwen3-coder                     # 로컬
"""

import argparse
import json

from agent import Agent, AgentConfig
from llm import DEFAULT_MODEL, get_llm
from tools import default_tools


def ask(what: str) -> bool:
    return input(f"\n허용할까요? {what}\n[y/N] ").strip().lower() in ("y", "yes")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="provider:model")
    ap.add_argument("--base-url", default=None, help="OpenAI 호환 서버 주소 override")
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--step-limit", type=int, default=30)
    ap.add_argument("--yes", action="store_true", help="도구 실행 전 확인을 건너뜀 (주의!)")
    args = ap.parse_args()

    llm = get_llm(args.model, effort=args.effort, base_url=args.base_url)
    tools = default_tools(args.cwd, confirm=None if args.yes else ask)
    result = Agent(llm, tools, AgentConfig(step_limit=args.step_limit, cwd=args.cwd)).run(args.task)
    print("\n=== result ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
