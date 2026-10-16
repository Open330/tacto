#!/usr/bin/env bash
# Build and sign the nlc launcher — the only client the helper accepts.
set -euo pipefail
cd "$(dirname "$0")"
. ../signing.sh
SWIFTC=/Library/Developer/CommandLineTools/usr/bin/swiftc
SDK=$(/bin/ls -d /Library/Developer/CommandLineTools/SDKs/MacOSX*.sdk | /usr/bin/tail -1)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp nlc.swift "$STAGE/main.swift"
"$SWIFTC" -O -sdk "$SDK" "$STAGE/main.swift" ../SigningPolicy.swift -o "$STAGE/nlc"
nlc_select_identity
nlc_sign "$STAGE/nlc" "$NLC_LAUNCHER_ID"
nlc_warn_team_change nlc "$STAGE/nlc"
mv "$STAGE/nlc" nlc
echo "built and verified: $(pwd)/nlc"
