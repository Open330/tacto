#!/usr/bin/env bash
# Build everything the permission boundary needs, in dependency order.
#
#   launcher/tacto        signed entry point
#   (the relay lives inside the launcher — a separate signed broker binary was
#    removed: anyone could run it, so authenticating it proved nothing)
#   helper/tacto-helper   the binary that holds the Accessibility grant
#   Tacto Helper.app      that binary packaged so TCC lists it by name
#
# Nothing here needs an Xcode licence: the Command Line Tools toolchain is used
# with an explicit SDK path.
set -euo pipefail
cd "$(dirname "$0")"
./launcher/build.sh
./helper/build.sh
./helper/bundle.sh
echo
echo "다음: ./skill/install.sh && ~/.local/bin/tacto doctor"
echo "실행: ~/.local/bin/tacto do \"계산기로 12 곱하기 34\" --helper"
