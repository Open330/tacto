#!/usr/bin/env bash
# Install NLC for coding agents: the `nlc` command on PATH, and the skill.
#
#   skill/install.sh           install (or repair) both
#   skill/install.sh --check   say what is installed and what is missing; change nothing
#
# Two things used to go wrong. This script copied SKILL.md and nothing else,
# so an agent read "run `nlc apps`" and found no `nlc` anywhere (reported from a
# Codex session, 2026-09-28). And the copies went stale: every installed copy
# still described the skill as it was on 2026-09-17. Now the command is a link
# to the signed launcher, and the skill is a link to this directory, so what an
# agent reads is always what this checkout says.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd -P)
skill="$repo/skill"
bin_dir="${NLC_BIN_DIR:-$HOME/.local/bin}"
check=0
[ "${1:-}" = "--check" ] && check=1

say() { printf '  %s\n' "$*"; }
ours() {   # is this path an NLC skill we may replace?
  [ -L "$1" ] && return 0
  [ -f "$1/SKILL.md" ] && grep -q '^name: nlc$' "$1/SKILL.md"
}

echo "NLC — $repo"

# 1. The command. The signed launcher is the entry point that holds the
#    permission boundary and works from any directory; the venv script is a
#    fallback for machines that have not built it.
entry=""
if [ -x "$repo/launcher/nlc" ]; then
  entry="$repo/launcher/nlc"
elif [ -x "$repo/.venv/bin/nlc" ]; then
  entry="$repo/.venv/bin/nlc"
fi
target="$bin_dir/nlc"
echo "command"
if [ -z "$entry" ]; then
  say "missing: no launcher/nlc and no .venv/bin/nlc — build first:"
  say "  uv sync && ./launcher/build.sh && ./helper/build.sh && ./helper/bundle.sh"
  [ $check = 1 ] || exit 1
elif [ $check = 1 ]; then
  if [ "$(readlink "$target" 2>/dev/null)" = "$entry" ]; then say "ok: $target -> $entry"
  else say "missing: $target (would link to $entry)"; fi
else
  mkdir -p "$bin_dir"
  if [ -e "$target" ] && [ ! -L "$target" ]; then
    say "not replacing $target: it is a file that is not ours"
    exit 1
  fi
  ln -sfn "$entry" "$target"
  say "linked: $target -> $entry"
  [ "$entry" = "$repo/.venv/bin/nlc" ] && \
    say "note: unsigned entry point; build launcher/ for the permission boundary"
fi
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) say "warning: $bin_dir is not on PATH — add it, or agents will not find nlc" ;;
esac

# 2. The skill, everywhere an agent reads skills: the default Claude Code and
#    Codex homes, the active ones if relocated, and asx profiles.
echo "skill"
dirs=("$HOME/.claude/skills" "$HOME/.codex/skills")
[ -n "${CLAUDE_CONFIG_DIR:-}" ] && dirs+=("$CLAUDE_CONFIG_DIR/skills")
[ -n "${CODEX_HOME:-}" ] && dirs+=("$CODEX_HOME/skills")
for d in "$HOME/Library/Application Support/asx/profiles"/*/skills; do
  [ -d "$d" ] && dirs+=("$d")
done
seen=""
for parent in "${dirs[@]}"; do
  [ -d "$parent" ] || continue
  real=$(cd "$parent" && pwd -P)
  case "$seen" in *"|$real|"*) continue ;; esac
  seen="$seen|$real|"
  dest="$parent/nlc"
  if [ $check = 1 ]; then
    if [ "$(readlink "$dest" 2>/dev/null)" = "$skill" ]; then say "ok: $dest"
    elif [ -e "$dest" ]; then say "stale copy: $dest"
    else say "missing: $dest"; fi
    continue
  fi
  if [ -e "$dest" ] && ! ours "$dest"; then
    say "not replacing $dest: it is not the NLC skill"
    continue
  fi
  rm -rf "$dest"
  ln -s "$skill" "$dest"
  say "linked: $dest"
done
