#!/bin/sh
# 모든 버전의 테스트를 실행한다 (API 키 불필요).
set -e
cd "$(dirname "$0")"
for d in v[0-9][0-9]_*/; do
  printf '%-22s ' "${d%/}"
  (cd "$d" && uv run python -m unittest discover -q tests 2>&1 | tail -1)
done
