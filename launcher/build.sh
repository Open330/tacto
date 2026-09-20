#!/usr/bin/env bash
# Build and sign the nlc launcher — the only client the helper accepts.
set -euo pipefail
cd "$(dirname "$0")"
. ../signing.sh
SWIFTC=/Library/Developer/CommandLineTools/usr/bin/swiftc
SDK=$(/bin/ls -d /Library/Developer/CommandLineTools/SDKs/MacOSX*.sdk | /usr/bin/tail -1)
"$SWIFTC" -O -sdk "$SDK" nlc.swift -o nlc
IDENTITY=${NLC_SIGN_IDENTITY:-$(security find-identity -p codesigning -v \
  | grep -v CSSMERR | head -1 | sed -E 's/.*"(.*)"/\1/')}
if [ -z "$IDENTITY" ]; then
  echo "no signing identity — the helper will refuse this launcher"; exit 1
fi
# A stable identifier the helper can require by name, not just by team, and a
# designated requirement that says the same thing this binary demands of the
# helper. Without -r codesign would pin the signer's certificate CN instead.
codesign --force --sign "$IDENTITY" --identifier "$NLC_LAUNCHER_ID" \
         -r="designated => $(nlc_requirement "$NLC_LAUNCHER_ID")" \
         --options runtime --timestamp=none nlc
# Prove the requirement we just stamped is one this signature satisfies. A
# certificate from another team would leave a requirement nothing can meet,
# and the failure would otherwise surface as a refused connection at runtime.
codesign --verify -R="$(nlc_requirement "$NLC_LAUNCHER_ID")" nlc
echo "signed: $(codesign -dv nlc 2>&1 | grep -E 'Identifier|TeamIdentifier' | tr '\n' ' ')"
echo "built: $(pwd)/nlc"
