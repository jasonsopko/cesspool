#!/usr/bin/env python3
"""Add who built each template to block and transaction records processed before that was stored.

add_builder.py [--force]

Reads the coinbase from each cached raw block, one block at a time, and writes "bld", "dtag" and
"via" into blocks/<h>.json and "bld" and "ws" into txd/<h>.json. A record that already has "via" is
left alone unless --force. Rerunning is harmless.
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cesspool import miner, process  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    hs = sorted(int(n[:-5]) for n in os.listdir(process.BLOCKS) if n.endswith(".json"))
    done = skipped = 0
    for h in hs:
        bp, tp = f"{process.BLOCKS}/{h}.json", f"{process.TXD}/{h}.json"
        with open(bp) as f:
            rec = json.load(f)
        if "via" in rec and not a.force:
            skipped += 1
            continue
        cb = process.load_raw(h)["tx"][0]
        bld, dtag, via = miner.builder(cb, rec["pool"])
        rec["bld"], rec["dtag"], rec["via"] = bld, dtag, via
        if os.path.exists(tp):
            with open(tp) as f:
                txd = json.load(f)
            txd["bld"], txd["ws"] = bld, miner.slug(miner.who(rec["pool"], via))
            process.write_json(tp, txd)
        process.write_json(bp, rec)
        done += 1
    print(f"updated {done}, already had it {skipped}")


if __name__ == "__main__":
    main()
