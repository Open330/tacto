#!/usr/bin/env bash
# Build and sign the broker — the only peer the helper will accept.
set -euo pipefail
cd "$(dirname "$0")"
SWIFTC=/Library/Developer/CommandLineTools/usr/bin/swiftc
SDK=$(/bin/ls -d /Library/Developer/CommandLineTools/SDKs/MacOSX*.sdk | /usr/bin/tail -1)
STAGE=$(mktemp -d); trap 'rm -rf "$STAGE"' EXIT
cp NLCBroker.swift "$STAGE/main.swift"
"$SWIFTC" -O -sdk "$SDK" "$STAGE/main.swift" -o nlc-broker
IDENTITY=${NLC_SIGN_IDENTITY:-$(security find-identity -p codesigning -v \
  | grep -v CSSMERR | head -1 | sed -E 's/.*"(.*)"/\1/')}
[ -n "$IDENTITY" ] || { echo "no signing identity"; exit 1; }
codesign --force --sign "$IDENTITY" --identifier "ai.callabo.nlc.broker" \
         --options runtime --timestamp=none nlc-broker
echo "signed: $(codesign -dv nlc-broker 2>&1 | grep Identifier= | head -1)"
echo "built: $(pwd)/nlc-broker"
