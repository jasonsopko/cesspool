#!/bin/bash
# Build the verdict engine into test_bitcoin from a Plumb tree at a release tag.
#   plumb-check/build.sh <plumb worktree with a configured build/> 
# The worktree must sit at the tag the site names (PLUMB_VERSION in site.py).
# Installs $CESSPOOL_HOME/bin/test_bitcoin-plumb (default ~/.cesspool); process.py uses it.
set -euo pipefail
tree=${1:?plumb worktree}
here=$(cd "$(dirname "$0")" && pwd)
cp "$here/plumb_check_tests.cpp" "$tree/src/test/"
grep -q plumb_check_tests.cpp "$tree/src/test/CMakeLists.txt" ||
  sed -i 's/^  policyestimator_tests.cpp$/  plumb_check_tests.cpp\n  policyestimator_tests.cpp/' "$tree/src/test/CMakeLists.txt"
git -C "$tree" describe --tags --always
cmake --build "$tree/build" --target test_bitcoin -j2
bin=${CESSPOOL_HOME:-$HOME/.cesspool}/bin
install -D -m 755 "$tree/build/bin/test_bitcoin" "$bin/test_bitcoin-plumb.new"
mv "$bin/test_bitcoin-plumb.new" "$bin/test_bitcoin-plumb"
sha256sum "$bin/test_bitcoin-plumb"
