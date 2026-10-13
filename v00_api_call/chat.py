"""v00: 가장 단순한 멀티턴 채팅 REPL.

    uv run python chat.py                          # Anthropic (기본)
    uv run python chat.py --provider openai --model gpt-5
    uv run python chat.py -p "파이썬 GIL을 한 문장으로"   # 한 번만 묻고 종료

"대화"가 실제로는 history 리스트를 매번 통째로 다시 보내는 것임을 확인해보자.
--show-request 를 켜면 매 요청에 실린 messages 개수가 출력된다.
"""

import argparse

import llm

SYSTEM = "You are a concise, helpful assistant. Answer in the user's language."


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "openai", "openrouter"])
    ap.add_argument("--model", default=None)
    ap.add_argument("-p", "--prompt", default=None, help="한 번만 질문하고 종료")
    ap.add_argument("--show-request", action="store_true")
    args = ap.parse_args()

    history = []  # 이 리스트가 곧 "대화 기억"이다. LLM 서버는 아무것도 기억하지 않는다.
    while True:
        user = args.prompt if args.prompt is not None else input("\nyou> ").strip()
        if user in ("exit", "quit"):
            break
        if not user:
            continue
        history.append({"role": "user", "content": user})
        if args.show_request:
            print(f"[request] messages={len(history)}")
        text, resp = llm.chat(args.provider, history, SYSTEM, args.model)
        history.append({"role": "assistant", "content": text})
        inp, out = llm.usage_of(args.provider, resp)
        print(f"\nassistant> {text}\n[tokens in={inp} out={out}]")
        if args.prompt is not None:
            break


if __name__ == "__main__":
    main()
