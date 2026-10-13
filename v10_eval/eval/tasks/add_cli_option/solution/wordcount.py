import argparse
import re
import sys
from collections import Counter


def count_words(text):
    return Counter(re.findall(r"[a-z']+", text.lower()))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Count words in a file")
    ap.add_argument("path")
    ap.add_argument("--top", type=int, default=0, metavar="N", help="also print the N most common words")
    args = ap.parse_args(argv)
    with open(args.path) as f:
        counts = count_words(f.read())
    print(f"total: {sum(counts.values())}")
    for word, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:args.top]:
        print(f"{word}: {n}")


if __name__ == "__main__":
    main()
