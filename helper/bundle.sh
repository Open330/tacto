#!/usr/bin/env bash
# Package the helper as an .app bundle.
#
# TCC identifies a client by code signature, and it lists it by bundle name. A
# bare binary shows up in System Settings as whatever the executable is called;
# a bundle shows up as "NLC Helper" with its own identity, keeps its grant
# across rebuilds of the same signature, and is what a user can reason about
# when deciding whether to allow it.
set -euo pipefail
cd "$(dirname "$0")"
APP="NLC Helper.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>NLC Helper</string>
  <key>CFBundleDisplayName</key>     <string>NLC Helper</string>
  <key>CFBundleIdentifier</key>      <string>ai.callabo.nlc.helper</string>
  <key>CFBundleExecutable</key>      <string>NLCHelper</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>CFBundleShortVersionString</key> <string>0.1.0</string>
  <key>CFBundleVersion</key>         <string>1</string>
  <key>LSMinimumSystemVersion</key>  <string>13.0</string>
  <!-- Background only: the helper never shows in the Dock or takes focus. -->
  <key>LSUIElement</key>             <true/>
  <key>NSAccessibilityUsageDescription</key>
  <string>NLC이 사용자를 대신해 앱의 버튼을 누르고 화면을 읽으려면 필요합니다. 조작 중에는 해당 창에 표시가 나타나며 Esc로 즉시 멈출 수 있습니다.</string>
</dict>
</plist>
PLIST

cp nlc-helper "$APP/Contents/MacOS/NLCHelper"

IDENTITY=${NLC_SIGN_IDENTITY:-$(security find-identity -p codesigning -v \
  | grep -v CSSMERR | head -1 | sed -E 's/.*"(.*)"/\1/')}
[ -n "$IDENTITY" ] || { echo "no signing identity"; exit 1; }
# Sign inner code first, then the bundle — Apple's guidance; --deep is for
# verification, not a signing strategy. Hardened runtime stays on: a process
# holding an accessibility grant should not accept library injection.
codesign --force --sign "$IDENTITY" --options runtime --timestamp=none \
         "$APP/Contents/MacOS/NLCHelper"
codesign --force --sign "$IDENTITY" --options runtime --timestamp=none "$APP"
codesign --verify --deep --strict "$APP" && echo "verified: hardened runtime, inner-first"
codesign -dv "$APP" 2>&1 | grep -E 'Identifier|TeamIdentifier'
echo "bundled: $(pwd)/$APP"
