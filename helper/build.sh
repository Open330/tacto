#!/usr/bin/env bash
# Build and sign the helper that holds the Accessibility grant.
#
# Swift only allows top-level statements in a file named main.swift, and the
# helper is split across several files now, so the entry point is staged under that
# name in a temp dir rather than renaming the source.
set -euo pipefail
cd "$(dirname "$0")"
. ../signing.sh
SWIFTC=/Library/Developer/CommandLineTools/usr/bin/swiftc
SDK=$(/bin/ls -d /Library/Developer/CommandLineTools/SDKs/MacOSX*.sdk | /usr/bin/tail -1)

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp NLCHelper.swift "$STAGE/main.swift"
cp PeerAuth.swift  "$STAGE/PeerAuth.swift"
cp WindowInput.swift "$STAGE/WindowInput.swift"

"$SWIFTC" -O -sdk "$SDK" "$STAGE/main.swift" "$STAGE/PeerAuth.swift" "$STAGE/WindowInput.swift" -o nlc-helper

IDENTITY=${NLC_SIGN_IDENTITY:-$(security find-identity -p codesigning -v \
  | grep -v CSSMERR | head -1 | sed -E 's/.*"(.*)"/\1/')}
if [ -n "$IDENTITY" ]; then
  # Same identifier and requirement as the bundled copy: the launcher
  # authenticates whichever one answers, and a name only it uses would fail.
  codesign --force --sign "$IDENTITY" --identifier "$NLC_HELPER_ID" \
           -r="designated => $(nlc_requirement "$NLC_HELPER_ID")" \
           --options runtime --timestamp=none nlc-helper
  codesign --verify -R="$(nlc_requirement "$NLC_HELPER_ID")" nlc-helper
  echo "signed with: $IDENTITY"
else
  echo "no signing identity found — built unsigned (grant will not survive rebuilds)"
fi
echo "built: $(pwd)/nlc-helper"
