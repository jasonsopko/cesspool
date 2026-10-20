#!/usr/bin/env python3
"""Add who built each template to block and transaction records processed before that was stored.

add_builder.py [--force] [--own]

Reads the coinbase from each cached raw block, one block at a time, and writes "bld", "dtag",
"via" and "own" into blocks/<h>.json and "bld" and "ws" into txd/<h>.json. A record that already
has "via" is left alone unless --force. --own reads only the DATUM-pool records the pool's own
gateway could claim (the ones charged to DATUM miners via the pool); the minute update does the
same for the pools concerned whenever sv1-jobs.py learns a new port identity. Rerunning is
harmless: a record is rewritten only when its reading changed.
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cesspool import process  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--own", action="store_true", help="only DATUM-pool blocks charged to DATUM miners via a pool")
    a = ap.parse_args()
    hs = sorted(int(n[:-5]) for n in os.listdir(process.BLOCKS) if n.endswith(".json"))
    done = skipped = 0
    for h in hs:
        with open(f"{process.BLOCKS}/{h}.json") as f:
            rec = json.load(f)
        if a.own:
            if rec.get("bld") != "D" or not rec.get("via"):
                skipped += 1
                continue
        elif "via" in rec and not a.force:
            skipped += 1
            continue
        if process.reread_builder(h, rec):
            done += 1
        else:
            skipped += 1
    print(f"updated {done}, unchanged {skipped}")


if __name__ == "__main__":
    main()
