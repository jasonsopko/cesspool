# cesspool.lol

Every Bitcoin block since the BLAKE2b fork (961640), every transaction in it,
graded for spam, with the pool that mined it and the filter that would have
kept it out.

## How it works

- `plumb-check/plumb_check_tests.cpp` is built into `test_bitcoin` from a Plumb
  tree (`plumb-check/build.sh`). It runs the shipped policy code on each
  transaction three ways, each with that software's defaults: Bitcoin Core 31 (`-corepolicy` plus Core 30's legacy-sigop limit and multiple OP_RETURN outputs), stock Knots 29.4.2
  (Plumb's filters off) and Plumb, and reports every reason each one
  trips plus where Plumb counts data bytes. Nothing reimplements a filter.
  The site shows the Knots and Plumb verdicts only: Bitcoin Core has no
  BLAKE2b proof of work, so no Core node follows this chain.
- `cesspool/classify.py` names the protocol and finds the shapes Plumb does
  not count yet (fake multisig keys), so a miss shows as a miss.
- `cesspool/miner.py` names the pool with reorg-watch's own rules.
- `cesspool/site.py` renders static pages and share images.
- `tools/update.py` runs from cron each minute: fetches new blocks over REST,
  reprocesses anything a reorg replaced, renders what changed.

The payload is never decoded or displayed.

## State

`~/.cesspool/`: `raw/` gzipped REST blocks (about 340 kB each), `blocks/`
one record per block, `index.json` summaries, `bin/test_bitcoin-plumb`.

Rebuild everything after a classifier change:

    rm ~/.cesspool/blocks/*.json ~/.cesspool/index.json
    tools/backfill.py 961640 <tip>
    tools/update.py --out /var/www/cesspool.lol --all

Setup on node1: `deploy/setup.sh`.
