# Native peers trust the same Apple-issued signing team, not one developer team.
TACTO_LAUNCHER_ID=ai.callabo.tacto
TACTO_HELPER_ID=ai.callabo.tacto.helper

tacto_select_identity() {
  if [ -n "${TACTO_SIGN_IDENTITY:-}" ]; then
    IDENTITY=$TACTO_SIGN_IDENTITY
  else
    IDENTITY=$(security find-identity -p codesigning -v | sed -nE '/CSSMERR/!s/^[[:space:]]*[0-9]+\) ([0-9A-Fa-f]{40}) .*/\1/p' | head -1)
  fi
  [ -n "$IDENTITY" ] && [ "$IDENTITY" != '-' ] || {
    echo 'An Apple-issued code signing identity is required; set TACTO_SIGN_IDENTITY.' >&2
    return 1
  }
}

tacto_requirement() {
  local identifier=$1 team=$2
  [[ "$identifier" =~ ^[a-zA-Z0-9.-]+$ && "$team" =~ ^[A-Z0-9]{10}$ ]] || {
    echo 'Invalid signing identifier or team.' >&2; return 1;
  }
  printf 'identifier "%s" and anchor apple generic and certificate leaf[subject.OU] = "%s"' "$identifier" "$team"
}

tacto_sign() {
  local target=$1 identifier=$2 metadata team requirement
  # First signature discovers the actual certificate team. Never trust a team
  # supplied by the runtime environment or assume the certificate's CN format.
  codesign --force --sign "$IDENTITY" --identifier "$identifier" \
    --options runtime --timestamp=none "$target" || return
  metadata=$(codesign -dv --verbose=4 "$target" 2>&1) || return
  team=$(printf '%s\n' "$metadata" | sed -n 's/^TeamIdentifier=//p')
  requirement=$(tacto_requirement "$identifier" "$team") || return
  codesign --force --sign "$IDENTITY" --identifier "$identifier" \
    -r="designated => $requirement" --options runtime --timestamp=none "$target" || return
  codesign --verify --strict -R="$requirement" "$target" || return
  printf 'Verified signing team: %s (%s)\n' "$team" "$identifier"
}

# Warn before replacing a signed artifact whose designated identity changed.
# macOS permission grants bind to both the identifier and the signing team.
tacto_warn_team_change() {
  local previous=$1 replacement=$2 old new old_id new_id
  [ -e "$previous" ] || return 0
  old=$(codesign -dv --verbose=4 "$previous" 2>&1 | sed -n 's/^TeamIdentifier=//p') || return 0
  new=$(codesign -dv --verbose=4 "$replacement" 2>&1 | sed -n 's/^TeamIdentifier=//p') || return 0
  old_id=$(codesign -dv --verbose=4 "$previous" 2>&1 | sed -n 's/^Identifier=//p') || return 0
  new_id=$(codesign -dv --verbose=4 "$replacement" 2>&1 | sed -n 's/^Identifier=//p') || return 0
  if [ -n "$old" ] && [ "$old" != "$new" ]; then
    printf 'Signing team changed: %s -> %s. macOS permissions may need to be granted again. Set TACTO_SIGN_IDENTITY to retain the intended team.\n' "$old" "$new" >&2
  fi
  if [ -n "$old_id" ] && [ "$old_id" != "$new_id" ]; then
    printf 'Signing identifier changed: %s -> %s. Grant macOS Accessibility and Screen Recording permissions to the new app identity.\n' "$old_id" "$new_id" >&2
  fi
}
