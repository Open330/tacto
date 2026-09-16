#!/usr/bin/env bash
# Build and sign the nlc launcher — the only client the helper accepts.
set -euo pipefail
cd "$(dirname "$0")"
SWIFTC=/Library/Developer/CommandLineTools/usr/bin/swiftc
SDK=$(/bin/ls -d /Library/Developer/CommandLineTools/SDKs/MacOSX*.sdk | /usr/bin/tail -1)
"$SWIFTC" -O -sdk "$SDK" nlc.swift -o nlc
IDENTITY=${NLC_SIGN_IDENTITY:-$(security find-identity -p codesigning -v \
  | grep -v CSSMERR | head -1 | sed -E 's/.*"(.*)"/\1/')}
if [ -z "$IDENTITY" ]; then
  echo "no signing identity — the helper will refuse this launcher"; exit 1
fi
# A stable identifier the helper can require by name, not just by team.
codesign --force --sign "$IDENTITY" --identifier "ai.callabo.nlc" \
         --options runtime --timestamp=none nlc
echo "signed: $(codesign -dv nlc 2>&1 | grep -E 'Identifier|TeamIdentifier' | tr '\n' ' ')"
echo "built: $(pwd)/nlc"
