#!/usr/bin/env bash
# Build and sign the tacto launcher — the only client the helper accepts.
set -euo pipefail
cd "$(dirname "$0")"
. ../signing.sh
SWIFTC=/Library/Developer/CommandLineTools/usr/bin/swiftc
SDK=$(/bin/ls -d /Library/Developer/CommandLineTools/SDKs/MacOSX*.sdk | /usr/bin/tail -1)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp tacto.swift "$STAGE/main.swift"
"$SWIFTC" -O -sdk "$SDK" "$STAGE/main.swift" ../SigningPolicy.swift ../HelperModeProtocol.swift ../RuntimeStatus.swift HelperModeClient.swift -o "$STAGE/tacto"
tacto_select_identity
tacto_sign "$STAGE/tacto" "$TACTO_LAUNCHER_ID"
tacto_warn_team_change tacto "$STAGE/tacto"
mv "$STAGE/tacto" tacto
echo "built and verified: $(pwd)/tacto"
