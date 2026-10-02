#!/usr/bin/env python3
"""Minute cron: fetch new blocks, undo reorged ones, render what changed.

update.py --out /var/www/cesspool.lol [--all]
"""
import argparse, fcntl, json, os, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cesspool import process, site  # noqa: E402

REST = "http://127.0.0.1:8332/rest"
REORG_DEPTH = 30


def get(path, timeout=60):
    with urllib.request.urlopen(f"{REST}/{path}", timeout=timeout) as r:
        return r.read()


def hash_at(h):
    return json.loads(get(f"blockhashbyheight/{h}.json"))["blockhash"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--all", action="store_true", help="re-render every block page")
    ap.add_argument("--no-og", action="store_true")
    a = ap.parse_args()

    os.makedirs(process.HOME, exist_ok=True)
    lock = open(f"{process.HOME}/lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return

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
        for h in range(redo_from, top + 1):
            for p in (f"{process.BLOCKS}/{h}.json", f"{process.RAW}/{h}.json.gz"):
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
    if new or a.all or redo_from is not None:
        site.build(a.out, heights=new, all_blocks=a.all, og=not a.no_og)


if __name__ == "__main__":
    main()
