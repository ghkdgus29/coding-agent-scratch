"""v02 CLI (Anthropic 전용 — v03에서 provider 추상화).

    uv run python main.py "hello.py 를 만들고 실행해줘" --cwd /tmp/play
"""

import argparse
import json

import llm
from agent import Agent, AgentConfig
from tools import default_tools


def ask(what: str) -> bool:
    return input(f"\n허용할까요? {what}\n[y/N] ").strip().lower() in ("y", "yes")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--model", default="anthropic:claude-opus-5-5", help="anthropic:<model> 만 지원")
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--step-limit", type=int, default=30)
    ap.add_argument("--yes", action="store_true", help="도구 실행 전 확인을 건너뜀 (주의!)")
    args = ap.parse_args()

    provider, model = llm.parse_model_spec(args.model)
    if provider != "anthropic":
        raise SystemExit("v02는 Anthropic tool calling만 지원합니다. (v03에서 OpenAI 등 추가)")

    def call(system, messages, tools):
        return llm.anthropic_messages(system, messages, tools, model=model or llm.DEFAULT_ANTHROPIC_MODEL,
                                      effort=args.effort)

    tools = default_tools(args.cwd, confirm=None if args.yes else ask)
    result = Agent(call, tools, AgentConfig(step_limit=args.step_limit, cwd=args.cwd)).run(args.task)
    print("\n=== result ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
