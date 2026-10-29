#!/usr/bin/env python3
"""The numbers the Lightning bitcoin.conf files quote (site.LN_FACTS): lightning_facts.py LO HI, counted over
every transaction in the cached blocks (~/.cesspool/raw, getblock verbosity 3, which carries each fee).

The Lightning files change three Knots settings: subdustfeepenalty=0, mempooltruc=enforce and
permitephemeral=anchor,-send,dust. Together they admit a version 3 transaction that pays less than the
1 sat/vB relay floor on its own, with at most one pay-to-anchor output under its 240-sat dust limit, when a
version 3 child pays the floor for both (validation.cpp: the floor exemption is TRUC-only under enforce,
and a TRUC parent's unconfirmed child must be TRUC). So the scan counts:
  txs: transactions, not counting coinbases.
  p2a_under_240: transactions with a pay-to-anchor output (OP_1 <0x4e73>) worth less than 240 sat.
  v3_sub_floor: version 3 transactions whose fee in sat is under their plain vsize, so under the floor on
     any sizing (Knots' own vsize adds data weight, which only raises the floor); v3_zero_fee of them pay
     nothing; v3_sub_floor_relayed have a version 3 child in the same block paying at least 1 sat/vB over
     parent plus child, the only way such a parent gets through package relay. The rest a miner took directly.
  ln: Lightning force closes, a transaction whose locktime's top byte is 0x20 and every input's sequence's
     top byte is 0x80, as BOLT 3 builds a commitment transaction; ln_anchor_pair those with two 330-sat P2WSH
     outputs; ln_run_hit those with three or more distinct P2WSH outputs sharing one value of 546 sat or less,
     which Plumb's -rejectfakeoutputs counts as data (policy.h DATA_OUTPUT_DUST_RUN, DATA_OUTPUT_RUN_MAX_VALUE).
  ln_taproot_revoke: transactions with an input that spends LND's simple taproot channel revocation leaf
     <32-byte key> OP_DROP <32-byte key> OP_CHECKSIG through the script path, as script.cpp
     IsLnTaprootRevokeLeaf matches it (knots#487): Knots 29.4.3 refuses them as non-standard data, Plumb 9 counts
     the 34 dropped bytes of each toward -datacarriersize.
Prints the dict as JSON; with --rows also the sub-floor version 3 transactions and their children."""
import gzip, json, os, sys
from collections import Counter, defaultdict
from multiprocessing import Pool

RAW = os.path.join(os.environ.get("CESSPOOL_HOME", os.path.expanduser("~/.cesspool")), "raw")
P2A = "51024e73"


def revoke_leaf(v):
    """True when the input spends a taproot output through the 68-byte leaf under a tapscript control block,
    past an annex if any."""
    if v.get("prevout", {}).get("scriptPubKey", {}).get("type") != "witness_v1_taproot":
        return False
    w = v.get("txinwitness") or []
    top = len(w)
    if top >= 2 and w[-1].startswith("50"):
        top -= 1
    if top < 2:
        return False
    control, leaf = bytes.fromhex(w[top - 1]), bytes.fromhex(w[top - 2])
    return (33 <= len(control) <= 33 + 128 * 32 and (len(control) - 33) % 32 == 0 and control[0] & 0xfe == 0xc0
            and len(leaf) == 68 and leaf[0] == 32 and leaf[33] == 0x75 and leaf[34] == 32 and leaf[67] == 0xac)


def one(h):
    with gzip.open(f"{RAW}/{h}.json.gz") as f:
        b = json.load(f)
    c = Counter()
    txs = b["tx"][1:]
    c["txs"] = len(txs)
    by_id = {t["txid"]: t for t in txs}
    rows = []
    for tx in txs:
        fee = round(float(tx.get("fee", 0)) * 1e8)
        if any(o["scriptPubKey"]["hex"] == P2A and round(o["value"] * 1e8) < 240 for o in tx["vout"]):
            c["p2a_under_240"] += 1
        if tx["version"] == 3 and fee < tx["vsize"]:
            c["v3_sub_floor"] += 1
            c["v3_zero_fee"] += fee == 0
            kids = [k for k in txs if any(v.get("txid") == tx["txid"] for v in k["vin"])]
            good = [k for k in kids if k["version"] == 3
                    and (fee + round(float(k.get("fee", 0)) * 1e8)) >= tx["vsize"] + k["vsize"]]
            c["v3_sub_floor_relayed"] += bool(good)
            rows.append({"height": h, "txid": tx["txid"], "fee": fee, "vsize": tx["vsize"],
                         "children": [(k["txid"], k["version"], round(float(k.get("fee", 0)) * 1e8), k["vsize"]) for k in kids]})
        c["ln_taproot_revoke"] += any(revoke_leaf(v) for v in tx["vin"])
        if (tx["locktime"] >> 24) == 0x20 and all((v["sequence"] >> 24) == 0x80 for v in tx["vin"]):
            c["ln"] += 1
            runs = defaultdict(set)
            n330 = 0
            for o in tx["vout"]:
                spk = o["scriptPubKey"]
                val = round(o["value"] * 1e8)
                if spk["type"] == "witness_v0_scripthash" and val <= 546:
                    runs[val].add(spk["hex"])
                    n330 += val == 330
            c["ln_anchor_pair"] += n330 >= 2
            c["ln_run_hit"] += any(len(p) >= 3 for p in runs.values())
    return c, rows


def main():
    lo, hi = int(sys.argv[1]), int(sys.argv[2])
    total, rows = Counter(), []
    with Pool(4) as p:
        for c, rr in p.imap_unordered(one, range(lo, hi + 1), chunksize=8):
            total.update(c)
            rows.extend(rr)
    keys = ["txs", "p2a_under_240", "v3_sub_floor", "v3_zero_fee", "v3_sub_floor_relayed", "ln", "ln_anchor_pair", "ln_run_hit",
            "ln_taproot_revoke"]
    out = {"lo": lo, "hi": hi} | {k: total.get(k, 0) for k in keys}
    if "--rows" in sys.argv:
        out["rows"] = sorted(rows, key=lambda r: r["height"])
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
