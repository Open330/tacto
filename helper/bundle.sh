#!/usr/bin/env bash
# Package the helper as an .app bundle.
#
# TCC identifies a client by code signature, and it lists it by bundle name. A
# bare binary shows up in System Settings as whatever the executable is called;
# a bundle shows up as "Tacto Helper" with its own identity, keeps its grant
# across rebuilds of the same signature, and is what a user can reason about
# when deciding whether to allow it.
set -euo pipefail
cd "$(dirname "$0")"
. ../signing.sh
STAGE=$(mktemp -d "./.tacto-helper-bundle.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
APP="$STAGE/NLC Helper.app"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"
cp ../docs/assets/tacto-app.icns "$APP/Contents/Resources/tacto-app.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>NLC Helper</string>
  <key>CFBundleDisplayName</key>     <string>NLC Helper</string>
  <key>CFBundleDevelopmentRegion</key> <string>en</string>
  <key>LSHasLocalizedDisplayName</key> <true/>
  <key>CFBundleIconFile</key>        <string>tacto-app.icns</string>
  <key>CFBundleIdentifier</key>      <string>ai.callabo.nlc.helper</string>
  <key>CFBundleExecutable</key>      <string>NLCHelper</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>CFBundleShortVersionString</key> <string>0.1.0</string>
  <key>CFBundleVersion</key>         <string>1</string>
  <key>LSMinimumSystemVersion</key>  <string>13.0</string>
  <!-- Background only: the helper never shows in the Dock or takes focus. -->
  <key>LSUIElement</key>             <true/>
  <key>NSAccessibilityUsageDescription</key>
  <string>Tacto가 사용자를 대신해 앱의 버튼을 누르고 화면을 읽는 데 필요합니다. 조작 중에는 메뉴 막대에 표시가 나타나며 Esc로 즉시 멈출 수 있습니다.</string>
</dict>
</plist>
PLIST

# The base name matches the retained filename; Finder then uses the localized
# product name. Bundle ID, executable, path and designated requirement stay put.
for language in en ko; do
  mkdir -p "$APP/Contents/Resources/$language.lproj"
  cat > "$APP/Contents/Resources/$language.lproj/InfoPlist.strings" <<'NAMES'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Tacto Helper</string>
<key>CFBundleDisplayName</key><string>Tacto Helper</string>
</dict></plist>
NAMES
done

cp nlc-helper "$APP/Contents/MacOS/NLCHelper"

nlc_select_identity
# Inner code first, then bundle; preserve the same team/identifier requirement.
nlc_sign "$APP/Contents/MacOS/NLCHelper" "$NLC_HELPER_ID"
nlc_sign "$APP" "$NLC_HELPER_ID"
codesign --verify --deep --strict "$APP"
DEST="NLC Helper.app"
nlc_warn_team_change "$DEST" "$APP"
[ ! -L "$DEST" ] || { echo 'Refusing symlink bundle destination.' >&2; exit 1; }
if [ -e "$DEST" ]; then mv "$DEST" "$STAGE/previous.app"; fi
if ! mv "$APP" "$DEST"; then
  [ ! -e "$STAGE/previous.app" ] || mv "$STAGE/previous.app" "$DEST"
  exit 1
fi
echo "bundled and verified: $(pwd)/$DEST"
