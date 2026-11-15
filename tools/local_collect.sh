#!/usr/bin/env bash
# 로컬 수집 → 빌드 → 커밋 → push.
# GitHub 러너 IP가 Akamai에 차단된 동안(2026-10-27 진단) Actions 대신 쓰는 수동 실행용 스크립트.
#
# 사용:  tools/local_collect.sh
#
# 1. 작업 트리가 깨끗한지 확인하고 origin/main 을 rebase 로 받는다 (Actions 수집 커밋과 충돌 방지)
# 2. collect.py · build.py 실행. 새 경기 수는 collect.py 가 GITHUB_OUTPUT 에 쓰는 new_matches 로 받는다
# 3. 변경이 있으면 git add -A → "chore(collect): 로컬 수집 반영 (+N matches)" 로 커밋
# 4. push (거부되면 pull --rebase 후 1회 재시도)
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -n "$(git status --porcelain)" ]; then
  echo "작업 트리에 커밋하지 않은 변경이 있습니다. 정리한 뒤 다시 실행하세요 (git add -A 로 함께 커밋되지 않도록)." >&2
  git status --short >&2
  exit 1
fi

git pull --rebase --quiet

out_file="$(mktemp)"
trap 'rm -f "$out_file"' EXIT

GITHUB_OUTPUT="$out_file" python3 collect.py

# collect.py 는 성공했을 때만 new_matches 를 쓴다. 실패(403 등)면 state.json 의 실패 횟수만 바뀌므로 되돌리고 끝낸다.
new_matches="$(sed -n 's/^new_matches=//p' "$out_file" | tail -n 1)"
if [ -z "$new_matches" ]; then
  echo "수집 실패: 위 로그를 확인하세요. meta/state.json 변경은 되돌립니다." >&2
  git checkout -- meta/state.json
  exit 1
fi

python3 build.py

git add -A
if git diff --cached --quiet; then
  echo "변경 없음 (+${new_matches} matches) — 커밋하지 않습니다."
  exit 0
fi

git commit --quiet -m "chore(collect): 로컬 수집 반영 (+${new_matches} matches)"
if ! git push --quiet; then
  git pull --rebase --quiet
  git push --quiet
fi
git log --oneline -1
