#!/usr/bin/env python3
"""Minute cron: fetch new blocks, undo reorged ones, render what changed.

update.py --out /var/www/cesspool.lol [--all | --pages] [--watch 57]

With --watch, it keeps checking the node's tip every WATCH_EVERY seconds for that long and
updates as soon as the best block changes, so a block shows in seconds instead of waiting
for the next minute.
"""
import argparse, fcntl, json, os, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cesspool import miner, process, site  # noqa: E402

REST = "http://127.0.0.1:8332/rest"
REORG_DEPTH = 30
WATCH_EVERY = 2


def get(path, timeout=60):
    with urllib.request.urlopen(f"{REST}/{path}", timeout=timeout) as r:
        return r.read()


def hash_at(h):
    return json.loads(get(f"blockhashbyheight/{h}.json"))["blockhash"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--all", action="store_true", help="re-render every block page")
    which.add_argument("--pages", action="store_true", help="re-render every block page without republishing transaction data")
    ap.add_argument("--no-og", action="store_true")
    ap.add_argument("--watch", type=float, metavar="S", help="keep checking the tip for S seconds")
    a = ap.parse_args()

    os.makedirs(process.HOME, exist_ok=True)
    if not a.watch:
        if not run(a) and (a.all or a.pages):
            # The minute cron skips quietly when another run holds the lock; a run asked for by hand says so.
            sys.exit("another update holds the lock: run it again when that one ends")
        return
    deadline = time.monotonic() + a.watch
    seen = None
    while True:
        # A failure ends the loop: the next minute's run tries again, as without --watch.
        best = json.loads(get("chaininfo.json", timeout=8))["bestblockhash"]
        if best != seen and run(a):
            seen = best
        if time.monotonic() + WATCH_EVERY >= deadline:
            return
        time.sleep(WATCH_EVERY)


def run(a):
    """One update. False when another run holds the lock."""
    with open(f"{process.HOME}/lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        update(a)
    return True


def update(a):
    tip = json.loads(get("chaininfo.json"))["blocks"]
    have = sorted(int(n[:-5]) for n in os.listdir(process.BLOCKS) if n.endswith(".json"))
    top = have[-1] if have else site.FORK - 1

    # A reorg replaces blocks we already processed: drop them from the fork point up.
    redo_from = None
    for h in range(max(site.FORK, top - REORG_DEPTH), top + 1):
        p = f"{process.BLOCKS}/{h}.json"
        if not os.path.exists(p):
            continue
        with open(p) as f:
            stored = json.load(f)["hash"]
        if h > tip or hash_at(h) != stored:
            redo_from = h
            break
    if redo_from is not None:
        print(f"reorg: reprocessing from {redo_from}", flush=True)
        site.drop_from_tx_index(a.out, set(range(redo_from, top + 1)))
        for h in range(redo_from, top + 1):
            for p in (f"{process.BLOCKS}/{h}.json", f"{process.RAW}/{h}.json.gz", f"{process.TXD}/{h}.json"):
                if os.path.exists(p):
                    os.remove(p)
        top = redo_from - 1

    new = list(range(top + 1, tip + 1))
    for h in new:
        path = f"{process.RAW}/{h}.json.gz"
        if not os.path.exists(path):
            import gzip
            raw = get(f"block/{hash_at(h)}.json")
            with gzip.open(path + ".tmp", "wb", compresslevel=6) as f:
                f.write(raw)
            os.replace(path + ".tmp", path)
    for i in range(0, len(new), 100):
        process.process_range(new[i:i + 100])
    flipped = apply_port_identities()
    if new or flipped or a.all or a.pages or redo_from is not None:
        site.build(a.out, heights=new + flipped, all_blocks=a.all, og=not a.no_og, pages=a.pages)


def apply_port_identities():
    """When reorg-watch's sv1-jobs.py has learned a new gateway identity behind a pool's public stratum
    port, read the blocks that identity could claim again. Returns the heights whose records changed."""
    marker = f"{process.HOME}/port-identities.applied"
    try:
        m = os.path.getmtime(miner.IDENTITIES)
    except OSError:
        return []
    try:
        with open(marker) as f:
            if float(f.read().strip() or 0) == m:
                return []
    except OSError:
        pass
    pairs = {(r["label"], r["secondary"]) for r in (json.load(open(miner.IDENTITIES)).get("identities") or [])
             if isinstance(r, dict) and "label" in r and "secondary" in r}
    try:
        with open(site.INDEX) as f:
            idx = {int(k): v for k, v in json.load(f).items()}
    except (OSError, ValueError):
        idx = {}
    changed = process.refresh_own(idx, pairs)
    if changed:
        print(f"port identities: {len(changed)} blocks now count toward their pool: {changed[:10]}", flush=True)
    with open(marker, "w") as f:
        f.write(repr(m))
    return changed


if __name__ == "__main__":
    main()
