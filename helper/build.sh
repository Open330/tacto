#!/usr/bin/env bash
# Build and sign the permission-holding helper.
# Uses the Command Line Tools toolchain, which needs no Xcode licence agreement.
set -euo pipefail
cd "$(dirname "$0")"
SWIFTC=/Library/Developer/CommandLineTools/usr/bin/swiftc
SDK=$(/bin/ls -d /Library/Developer/CommandLineTools/SDKs/MacOSX*.sdk | /usr/bin/tail -1)
"$SWIFTC" -O -sdk "$SDK" NLCHelper.swift -o nlc-helper
IDENTITY=${NLC_SIGN_IDENTITY:-$(security find-identity -p codesigning -v \
  | grep -v CSSMERR | head -1 | sed -E 's/.*"(.*)"/\1/')}
if [ -n "$IDENTITY" ]; then
  codesign --force --sign "$IDENTITY" --options runtime --timestamp=none nlc-helper
  echo "signed with: $IDENTITY"
else
  echo "no signing identity found — built unsigned (grant will not survive rebuilds)"
fi
echo "built: $(pwd)/nlc-helper"
