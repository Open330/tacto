#!/bin/bash
# Spike only: builds TactoSpike.app (host + launchd agent) and spike-client.
# The agent is signed as the installed helper's identity so a run shows
# whether its existing macOS grants carry over when launchd starts it.
set -euo pipefail
cd "$(dirname "$0")"
. ../../signing.sh
OUT=${1:?usage: build.sh <output dir>}
SDK=$(xcrun --show-sdk-path)
IDENTITY=${TACTO_SIGN_IDENTITY:?set TACTO_SIGN_IDENTITY to the Developer ID Application hash}
APP="$OUT/TactoSpike.app"
AGENT_APP="$APP/Contents/Resources/Tacto Helper Spike.app"
rm -rf "$APP" "$OUT/spike-client"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Library/LaunchAgents" "$AGENT_APP/Contents/MacOS"
STAGE=$(mktemp -d); trap 'rm -rf "$STAGE"' EXIT

compile() { # name main.swift-source output
  mkdir -p "$STAGE/$1"; cp "$2" "$STAGE/$1/main.swift"; cp Shared.swift "$STAGE/$1/"
  xcrun swiftc -O -sdk "$SDK" "$STAGE/$1/main.swift" "$STAGE/$1/Shared.swift" -o "$3"
}
compile agent Agent.swift "$AGENT_APP/Contents/MacOS/TactoHelper"
compile host Host.swift "$APP/Contents/MacOS/TactoSpike"
compile client Client.swift "$OUT/spike-client"

plist() { /usr/libexec/PlistBuddy -c "$2" "$1" >/dev/null; }
I="$AGENT_APP/Contents/Info.plist"
plist "$I" "Add :CFBundleIdentifier string $TACTO_HELPER_ID"
plist "$I" "Add :CFBundleExecutable string TactoHelper"
plist "$I" "Add :CFBundlePackageType string APPL"
plist "$I" "Add :LSUIElement bool true"
H="$APP/Contents/Info.plist"
plist "$H" "Add :CFBundleIdentifier string ai.callabo.tacto.spike.host"
plist "$H" "Add :CFBundleExecutable string TactoSpike"
plist "$H" "Add :CFBundlePackageType string APPL"
plist "$H" "Add :LSUIElement bool true"
A="$APP/Contents/Library/LaunchAgents/ai.callabo.tacto.spike.agent.plist"
plist "$A" "Add :Label string ai.callabo.tacto.spike.agent"
plist "$A" "Add :BundleProgram string Contents/Resources/Tacto Helper Spike.app/Contents/MacOS/TactoHelper"
plist "$A" "Add :MachServices dict"
plist "$A" "Add :MachServices:ai.callabo.tacto.spike.xpc bool true"
plist "$A" "Add :AssociatedBundleIdentifiers array"
plist "$A" "Add :AssociatedBundleIdentifiers:0 string ai.callabo.tacto.spike.host"
plist "$A" "Add :ProcessType string Interactive"

tacto_sign "$AGENT_APP" "$TACTO_HELPER_ID"
tacto_sign "$APP" ai.callabo.tacto.spike.host
tacto_sign "$OUT/spike-client" "$TACTO_LAUNCHER_ID"
echo "built: $APP and $OUT/spike-client"
