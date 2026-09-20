#!/usr/bin/env bash
# Publish data/decisions/*.json to branch xintel/signals for Mac/0xbot git poller.
# Only shared channel between Grok sandbox and Jacob's Mac.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BRANCH="${XINTEL_SIGNALS_BRANCH:-xintel/signals}"
REMOTE="${XINTEL_SIGNALS_REMOTE:-origin}"

# Collect decision files (may be empty — README-only publish still allowed)
mapfile -t FILES < <(find data/decisions -maxdepth 1 -type f -name '*.json' ! -name '.gitkeep' | sort || true)

# Skip test/shadow artifacts unless FORCE_PUBLISH_TESTS=1
# Retired: calibration_shadow | shadow_only | pipe_check | PIPECHECK
PUB=()
for f in "${FILES[@]:-}"; do
  [[ -z "${f:-}" ]] && continue
  if [[ "${FORCE_PUBLISH_TESTS:-0}" != "1" ]]; then
    if grep -qE 'pipe_check|"PIPECHECK"|calibration_shadow|shadow_only' "$f" 2>/dev/null; then
      echo "skip test/shadow artifact $f"
      continue
    fi
  fi
  PUB+=("$f")
done

if [[ ${#PUB[@]} -eq 0 ]]; then
  echo "no real decision json to publish — will still sync README if changed"
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
for f in "${PUB[@]:-}"; do
  [[ -z "${f:-}" ]] && continue
  cp "$f" "$TMP/wt/data/decisions/"
done
# keep inbox optional (skip if only shadow lines — copy as-is; consumers filter flags)
[[ -f data/decisions/inbox.jsonl ]] && cp data/decisions/inbox.jsonl "$TMP/wt/data/decisions/" || true

cd "$TMP/wt"
git add data/decisions
if git diff --cached --quiet; then
  echo "no changes to push"
  exit 0
fi
git -c user.email="xintel-bot@local" -c user.name="xintel-publisher" commit -m "xintel: publish decisions $(date -u +%Y-%m-%dT%H:%MZ)"
git push -u "$REMOTE" "$BRANCH"
SHA="$(git rev-parse HEAD)"
echo "published to $REMOTE/$BRANCH"
echo "commit_sha=$SHA"
echo "contents_api_url=https://api.github.com/repos/jacobcr96-ops/0xbot/contents/data/decisions/README.md?ref=${BRANCH}"
for f in "${PUB[@]:-}"; do
  [[ -z "${f:-}" ]] && continue
  name="$(basename "$f")"
  echo "contents_api_url=https://api.github.com/repos/jacobcr96-ops/0xbot/contents/data/decisions/${name}?ref=${BRANCH}"
done
