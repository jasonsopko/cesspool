# cesspool.lol

Every Bitcoin block since the fork at 961640, every transaction in it, graded
for spam, with the pool that mined it and the filter that would have kept it
out.

## How it works

- `plumb-check/plumb_check_tests.cpp` is built into `test_bitcoin` from a Plumb
  tree (`plumb-check/build.sh`). It runs the shipped policy code on each
  transaction three ways, each with that software's defaults: Bitcoin Core 31 (`-corepolicy` plus Core 30's legacy-sigop limit and multiple OP_RETURN outputs), stock Knots 29.4.2
  (Plumb's filters off and `-datacarriersize` at Knots' 83) and Plumb (42 since plumb7), and reports every reason each one
  trips and the data bytes it counts, plus, for Plumb, where the bytes sit
  and how many each of its four data-counting filters adds (the count with
  that filter alone off), and, when Plumb's 42-byte limit changes its reasons, the
  reasons it gives at 83 (`p83`). Nothing reimplements a filter.
  The site shows the Knots and Plumb verdicts only: Bitcoin Core does not
  accept block 961640 or any block after it, so no Core node has these blocks.
- `cesspool/classify.py` names the protocol and finds the shapes Plumb does
  not count yet (fake multisig keys), so a miss shows as a miss.
- `cesspool/miner.py` names the pool with reorg-watch's own rules.
- `cesspool/site.py` renders static pages and share images.
- `cesspool/found.py` writes the indexes behind Your Blocks (`/found/`): pool
  names, coinbase tags and payout addresses to block heights, as static JSON
  under `/d/` that `site.js` searches in the browser.
- `tools/update.py` runs from cron each minute with `--watch 57`: it checks the
  node's tip every 2 seconds, and when the best block changes fetches new
  blocks over REST, reprocesses anything a reorg replaced, renders what
  changed.

The payload is never decoded or displayed.

A `--watch` run keeps the code it started with until it ends, 57 seconds after
it started or later if an update is still running then. After a code change,
wait for it to end before counting on the new code: until then a build can
write pages from the old code next to the new static files.

## State

`~/.cesspool/`: `raw/` gzipped REST blocks (about 340 kB each), `blocks/`
one record per block, `index.json` summaries, `found.json` each block's
coinbase tag and paying outputs, `bin/test_bitcoin-plumb`.

Rebuild everything after a classifier change:

    rm ~/.cesspool/blocks/*.json ~/.cesspool/index.json
    tools/backfill.py 961640 <tip>
    tools/update.py --out /var/www/cesspool.lol --all

Setup on node1: `deploy/setup.sh`.
