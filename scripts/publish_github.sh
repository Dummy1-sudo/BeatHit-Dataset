#!/usr/bin/env bash
set -euo pipefail
REPO_NAME="${1:-BeatHit-Dataset}"
if ! command -v gh >/dev/null; then
  echo "GitHub CLI (gh) is required." >&2; exit 2
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "Run: gh auth login" >&2; exit 2
fi
if [ ! -d .git ]; then git init -b main; fi
git add .
git commit -m "Add BeatHit high-accuracy dataset builder" || true
if ! git remote get-url origin >/dev/null 2>&1; then
  gh repo create "$REPO_NAME" --public --source=. --remote=origin --push
else
  git push -u origin HEAD
fi
