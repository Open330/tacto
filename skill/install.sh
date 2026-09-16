#!/usr/bin/env bash
# Install the NLC skill for Claude Code and/or Codex.
set -euo pipefail
cd "$(dirname "$0")"
for dest in "$HOME/.claude/skills/nlc" "$HOME/.codex/skills/nlc"; do
  parent=$(dirname "$dest")
  [ -d "$parent" ] || continue
  mkdir -p "$dest"
  cp SKILL.md "$dest/SKILL.md"
  echo "installed: $dest/SKILL.md"
done
