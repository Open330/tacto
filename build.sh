#!/usr/bin/env bash
# Build everything the permission boundary needs, in dependency order.
#
#   launcher/nlc        signed entry point — the only client the helper accepts
#   helper/nlc-helper   the binary that holds the Accessibility grant
#   NLC Helper.app      that binary packaged so TCC lists it by name
#
# Nothing here needs an Xcode licence: the Command Line Tools toolchain is used
# with an explicit SDK path.
set -euo pipefail
cd "$(dirname "$0")"
./launcher/build.sh
./helper/build.sh
./helper/bundle.sh
echo
echo "다음: ./launcher/nlc doctor    (권한을 도우미에 부여)"
echo "실행: ./launcher/nlc do \"계산기로 12 곱하기 34\" --helper"
