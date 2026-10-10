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

skill_dir=$(cd "$(dirname "$0")" && pwd -P)
repo=$(cd "$skill_dir/.." && pwd -P)
skill="$repo/skill"
bin_dir="${NLC_BIN_DIR:-$HOME/.local/bin}"
check=0
case "$#:${1:-}" in
  0:) ;;
  1:--check) check=1 ;;
  1:--help) echo 'usage: skill/install.sh [--check]'; exit 0 ;;
  *) echo 'usage: skill/install.sh [--check]' >&2; exit 2 ;;
esac
status=0

say() { printf '  %s\n' "$*"; }
ours() {   # is this path an NLC skill we may replace?
  [ "$(readlink "$1" 2>/dev/null)" = "$skill" ] && return 0
  [ -f "$1/SKILL.md" ] && grep -q '^name: nlc$' "$1/SKILL.md"
}
our_command_link() {
  local old old_repo
  old=$(readlink "$1" 2>/dev/null) || return 1
  case "$old" in
    "$repo/launcher/nlc"|"$repo/.venv/bin/nlc") return 0 ;;
    /*/launcher/nlc) old_repo=${old%/launcher/nlc} ;;
    /*/.venv/bin/nlc) old_repo=${old%/.venv/bin/nlc} ;;
    *) return 1 ;;
  esac
  [ -f "$old_repo/skill/SKILL.md" ] && grep -q '^name: nlc$' "$old_repo/skill/SKILL.md"
}

echo "NLC — $repo"

# 1. The command. The signed launcher is the entry point that holds the
#    permission boundary and works from any directory. A Python console script
#    cannot replace that boundary, even when it has the same command name.
entry=""
if [ -x "$repo/launcher/nlc" ]; then
  entry="$repo/launcher/nlc"
fi
target="$bin_dir/nlc"
echo "command"
if [ -z "$entry" ]; then
  say "missing: launcher/nlc — build the native checkout first:"
  say "  uv sync && ./build.sh"
  exit 1
elif [ $check = 1 ]; then
  if [ "$(readlink "$target" 2>/dev/null)" = "$entry" ]; then say "ok: $target -> $entry"
  else say "missing or conflicting: $target (would link to $entry)"; status=1; fi
else
  mkdir -p "$bin_dir"
  if { [ -e "$target" ] || [ -L "$target" ]; } && ! our_command_link "$target"; then
    say "not replacing $target: it is not our command link"
    exit 1
  fi
  ln -sfn "$entry" "$target"
  say "linked: $target -> $entry"
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
  if [ ! -d "$parent" ]; then
    if [ $check = 1 ]; then
      say "missing: $parent/nlc"; status=1; continue
    fi
    mkdir -p "$parent"
  fi
  real=$(cd "$parent" && pwd -P)
  case "$seen" in *"|$real|"*) continue ;; esac
  seen="$seen|$real|"
  dest="$parent/nlc"
  if [ $check = 1 ]; then
    if [ "$(readlink "$dest" 2>/dev/null)" = "$skill" ]; then say "ok: $dest"
    elif [ -e "$dest" ] || [ -L "$dest" ]; then say "stale copy or conflicting link: $dest"; status=1
    else say "missing: $dest"; status=1; fi
    continue
  fi
  if { [ -e "$dest" ] || [ -L "$dest" ]; } && ! ours "$dest"; then
    say "not replacing $dest: it is not the NLC skill"
    if [ -L "$dest" ] && [ ! -e "$dest" ]; then
      say "link target is missing; verify the previous NLC checkout before replacing this link"
    fi
    status=1
    continue
  fi
  if [ -L "$dest" ]; then
    ln -sfn "$skill" "$dest"
  else
    if [ -e "$dest" ]; then
      # Keep old SKILL.md outside the discovery root to avoid duplicate skills.
      backup=$(mktemp -d "$(dirname "$parent")/.nlc-skill-backup.XXXXXX")
      mv "$dest" "$backup/nlc"
      say "kept previous copy: $backup/nlc"
    fi
    ln -s "$skill" "$dest"
  fi
  say "linked: $dest"
done
exit "$status"
