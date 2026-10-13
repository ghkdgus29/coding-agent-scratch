import argparse
import re
import sys
from collections import Counter


def count_words(text):
    return Counter(re.findall(r"[a-z']+", text.lower()))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Count words in a file")
    ap.add_argument("path")
    args = ap.parse_args(argv)
    with open(args.path) as f:
        counts = count_words(f.read())
    print(f"total: {sum(counts.values())}")


if __name__ == "__main__":
    main()
