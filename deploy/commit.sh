#!/bin/bash
# First commit of the cesspool repo, signed with your key. Local only.
set -euo pipefail
cd "$(dirname "$0")/.."
git add -A
git status --short
git commit -S -F - <<'MSG'
Grade every block since the fork for spam

Each transaction goes through Plumb plumb1's own policy code three times
(Core defaults, stock Knots 29.4.2, Plumb), via a test_bitcoin case built
from the tagged tree, so the site and a Plumb node cannot disagree. A
small classifier names the protocol and flags fake multisig reveals,
which Plumb does not count yet, as misses rather than hiding them.

Backfilled 961640 through 975101 from node1's REST interface: 22,784
sewage transactions, 2.5% of block space; Plumb refuses 86% of it.
Funding tx b1723771 at 974246 counts 12,259 data bytes, matching the
#389 corpus run. Pool names use reorg-watch's attribution.
MSG
git log -1 --format='%H %G? %GS'
