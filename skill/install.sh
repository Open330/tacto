#!/usr/bin/env bash
# Tacto command + agent skill installer; retains the signed nlc command alias.
set -euo pipefail
skill_dir=$(cd "$(dirname "$0")" && pwd -P)
repo=$(cd "$skill_dir/.." && pwd -P)
skill="$repo/skill"
bin_dir="${TACTO_BIN_DIR:-${NLC_BIN_DIR:-$HOME/.local/bin}}"
check=0
case "$#:${1:-}" in
  0:) ;;
  1:--check) check=1 ;;
  1:--help) echo 'usage: skill/install.sh [--check]'; exit 0 ;;
  *) echo 'usage: skill/install.sh [--check]' >&2; exit 2 ;;
esac
status=0
say() { printf '  %s\n' "$*"; }
marker() { [ -f "$1/SKILL.md" ] && grep -Eq '^name: (nlc|tacto)$' "$1/SKILL.md"; }
ours() {
  [ "$(readlink "$1" 2>/dev/null)" = "$skill" ] && return 0
  marker "$1"
}
our_command_link() {
  local old old_repo
  old=$(readlink "$1" 2>/dev/null) || return 1
  case "$old" in
    "$repo/launcher/tacto"|"$repo/launcher/nlc"|"$repo/.venv/bin/nlc"|"$repo/.venv/bin/tacto") return 0 ;;
    /*/launcher/tacto) old_repo=${old%/launcher/tacto} ;;
    /*/launcher/nlc) old_repo=${old%/launcher/nlc} ;;
    /*/.venv/bin/nlc) old_repo=${old%/.venv/bin/nlc} ;;
    /*/.venv/bin/tacto) old_repo=${old%/.venv/bin/tacto} ;;
    *) return 1 ;;
  esac
  marker "$old_repo/skill"
}
backup_skill() {
  local destination="$1" parent="$2" backup
  backup=$(mktemp -d "$(dirname "$parent")/.tacto-skill-backup.XXXXXX")
  mv "$destination" "$backup/$(basename "$destination")"
  say "kept previous copy: $backup/$(basename "$destination")"
}
echo "Tacto — $repo"
if ! marker "$skill"; then
  say 'missing or invalid: skill/SKILL.md — restore the Tacto skill before installing'; exit 1
fi
entry="$repo/launcher/tacto"
if [ ! -x "$entry" ]; then
  say 'missing: launcher/tacto — build the native checkout first: uv sync && ./build.sh'; exit 1
fi
# Preflight both names before modifying either. Never replace another program.
for command_name in tacto nlc; do
  target="$bin_dir/$command_name"
  if [ "$check" = 1 ]; then
    if [ "$(readlink "$target" 2>/dev/null)" != "$entry" ]; then
      say "missing or conflicting: $target (would link to $entry)"; status=1
    else say "ok: $target -> $entry"; fi
  elif { [ -e "$target" ] || [ -L "$target" ]; } && ! our_command_link "$target"; then
    say "not replacing $target: it is not our command link"; exit 1
  fi
done
if [ "$check" = 0 ]; then
  mkdir -p "$bin_dir"
  for command_name in tacto nlc; do
    ln -sfn "$entry" "$bin_dir/$command_name"; say "linked: $bin_dir/$command_name -> $entry"
  done
fi
case ":$PATH:" in *":$bin_dir:"*) ;; *) say "warning: $bin_dir is not on PATH; use $bin_dir/tacto" ;; esac
dirs=("$HOME/.claude/skills" "$HOME/.codex/skills")
[ -n "${CLAUDE_CONFIG_DIR:-}" ] && dirs+=("$CLAUDE_CONFIG_DIR/skills")
[ -n "${CODEX_HOME:-}" ] && dirs+=("$CODEX_HOME/skills")
for parent in "$HOME/Library/Application Support/asx/profiles"/*/skills; do
  [ -d "$parent" ] && dirs+=("$parent")
done
seen=''
for parent in "${dirs[@]}"; do
  if [ ! -d "$parent" ]; then
    if [ "$check" = 1 ]; then say "missing: $parent/tacto"; status=1; continue; fi
    mkdir -p "$parent"
  fi
  real=$(cd "$parent" && pwd -P)
  case "$seen" in *"|$real|"*) continue ;; esac
  seen="$seen|$real|"
  dest="$parent/tacto"; legacy="$parent/nlc"
  if [ "$check" = 1 ]; then
    if [ "$(readlink "$dest" 2>/dev/null)" = "$skill" ]; then say "ok: $dest"
    elif [ -e "$dest" ] || [ -L "$dest" ]; then say "stale copy or conflicting link: $dest"; status=1
    else say "missing: $dest"; status=1; fi
    if { [ -e "$legacy" ] || [ -L "$legacy" ]; } && ours "$legacy"; then
      say "legacy NLC skill needs migration: $legacy"; status=1
    fi
    continue
  fi
  if { [ -e "$dest" ] || [ -L "$dest" ]; } && ! ours "$dest"; then
    say "not replacing $dest: it is not the Tacto skill"; status=1; continue
  fi
  if [ -L "$dest" ]; then ln -sfn "$skill" "$dest"
  else
    [ ! -e "$dest" ] || backup_skill "$dest" "$parent"
    ln -s "$skill" "$dest"
  fi
  say "linked: $dest"
  # Remove duplicate discovery only when the legacy skill is identifiable.
  # Back up even owned links/copies outside the skills directory.
  if { [ -e "$legacy" ] || [ -L "$legacy" ]; } && ours "$legacy"; then
    backup_skill "$legacy" "$parent"
  fi
done
exit "$status"
