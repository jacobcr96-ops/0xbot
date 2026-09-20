#!/usr/bin/env bash
# Publish data/decisions/*.json to branch xintel/signals for Mac/0xbot git poller.
# Only shared channel between Grok sandbox and Jacob's Mac.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BRANCH="${XINTEL_SIGNALS_BRANCH:-xintel/signals}"
REMOTE="${XINTEL_SIGNALS_REMOTE:-origin}"

# Collect non-test decision files
mapfile -t FILES < <(find data/decisions -maxdepth 1 -type f -name '*.json' ! -name '.gitkeep' | sort)
if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "no decision json to publish"
  exit 0
fi

# Skip pure pipe_check-only publishes unless FORCE_PUBLISH_TESTS=1
PUB=()
for f in "${FILES[@]}"; do
  if grep -q 'pipe_check\|"PIPECHECK"' "$f" 2>/dev/null && [[ "${FORCE_PUBLISH_TESTS:-0}" != "1" ]]; then
    echo "skip test artifact $f"
    continue
  fi
  PUB+=("$f")
done
if [[ ${#PUB[@]} -eq 0 ]]; then
  echo "nothing non-test to publish"
  exit 0
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git fetch "$REMOTE" "$BRANCH" 2>/dev/null || true
if git show-ref --verify --quiet "refs/remotes/$REMOTE/$BRANCH"; then
  git worktree add -B "$BRANCH" "$TMP/wt" "$REMOTE/$BRANCH"
else
  git worktree add --detach "$TMP/wt" HEAD
  git -C "$TMP/wt" checkout --orphan "$BRANCH"
  git -C "$TMP/wt" rm -rf . >/dev/null 2>&1 || true
fi

mkdir -p "$TMP/wt/data/decisions"
cp data/decisions/README.md "$TMP/wt/data/decisions/README.md" 2>/dev/null || true
for f in "${PUB[@]}"; do
  cp "$f" "$TMP/wt/data/decisions/"
done
# keep inbox optional
[[ -f data/decisions/inbox.jsonl ]] && cp data/decisions/inbox.jsonl "$TMP/wt/data/decisions/" || true

cd "$TMP/wt"
git add data/decisions
if git diff --cached --quiet; then
  echo "no changes to push"
  exit 0
fi
git -c user.email="xintel-bot@local" -c user.name="xintel-publisher" commit -m "xintel: publish decisions $(date -u +%Y-%m-%dT%H:%MZ)"
git push -u "$REMOTE" "$BRANCH"
echo "published to $REMOTE/$BRANCH"
