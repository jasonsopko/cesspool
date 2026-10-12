#!/usr/bin/env python3
"""The numbers the recommended files quote for -bytespersigopstrict and -maxscriptsize:
sigop_facts.py LO HI [VALUE] [MSS], counted over every transaction in the cached blocks.

plumb-check finds a setting's value only for the transactions the site shows, so the cost side,
what a value refuses among all the others, is counted here from the raw blocks with the same
arithmetic as Knots' checks (both checked against plumb-check's "fix" and a regtest node):
  -bytespersigopstrict S refuses when accurate sigops > vsize * 4 // S (validation.cpp PreChecks),
     so the smallest refusing S is vsize * 4 // accurate + 1. vsize leaves out -datacarriercost's
     extra weight, which only raises it, so a count here can only be too high.
  -maxscriptsize M refuses when a scriptSig, an input or output script or one input's
     serialized witness stack is over M (policy.cpp), so the largest refusing M is that size - 1.
Each transaction is sorted by the site's records: sewage, gray or neither (clean), and whether
Knots and Plumb relay it at their defaults."""
import gzip, json, os, sys
from collections import Counter
from multiprocessing import Pool

HOME = os.environ.get("CESSPOOL_HOME", os.path.expanduser("~/.cesspool"))


def ops(b):
    out, i = [], 0
    while i < len(b):
        o = b[i]; i += 1
        if 1 <= o <= 75: n = o
        elif o == 76: n = b[i] if i < len(b) else 0; i += 1
        elif o == 77: n = int.from_bytes(b[i:i + 2], "little"); i += 2
        elif o == 78: n = int.from_bytes(b[i:i + 4], "little"); i += 4
        else:
            out.append((o, None)); continue
        if i + n > len(b): return out, False
        out.append((o, b[i:i + n])); i += n
    return out, True


def sigops(b, accurate):
    """CScript::GetSigOpCount: stops at a bad push."""
    n, last = 0, None
    for o, d in ops(b)[0]:
        if o in (0xac, 0xad): n += 1
        elif o in (0xae, 0xaf): n += (last - 0x50) if accurate and last is not None and 0x51 <= last <= 0x60 else 20
        last = o
    return n


def cs(n):
    return 1 if n < 253 else 3 if n <= 0xffff else 5


def kind(v):
    """A short name for the script an input spends: m-of-n multisig, a tapscript's key count, or its type."""
    t = v["prevout"]["scriptPubKey"]["type"]
    w = v.get("txinwitness") or []
    st = w[:-1] if len(w) >= 2 and w[-1].startswith("50") else w  # drop an annex
    if t == "witness_v1_taproot" and len(st) >= 2:
        s = ops(bytes.fromhex(st[-2]))[0]
        k = [o for o, _ in s].count(0xba)
        return f"tapscript, {k + 1} keys" if k else "tapscript"
    if w and t in ("witness_v0_scripthash", "scripthash"):
        script = bytes.fromhex(w[-1])
    elif t == "scripthash":
        s = ops(bytes.fromhex(v["scriptSig"]["hex"]))[0]
        script = s[-1][1] if s and s[-1][1] is not None else b""
    else:
        return t
    s = ops(script)[0]
    c = [o for o, _ in s]
    if len(c) >= 4 and c[-1] == 0xae and 0x51 <= c[0] <= 0x60 and 0x51 <= c[-2] <= 0x60 and len(c) == c[-2] - 0x50 + 3:
        return f"{c[0] - 0x50}-of-{c[-2] - 0x50} multisig"
    keys = sum(1 for _, d in s if d is not None and len(d) == 33)
    return f"multisig with other branches, {keys} keys" if 0xae in c or 0xaf in c else "other script"


def one(h):
    with gzip.open(f"{HOME}/raw/{h}.json.gz") as f:
        b = json.load(f)
    with open(f"{HOME}/blocks/{h}.json") as f:
        shown = {s["txid"]: s for s in json.load(f).get("spam", [])}
    rows = []
    for tx in b["tx"][1:]:
        big, big_in, leg, p2sh, wit = 0, None, 0, 0, 0
        for v in tx["vin"]:
            ss = bytes.fromhex(v["scriptSig"]["hex"])
            spk = v["prevout"]["scriptPubKey"]
            w = v.get("txinwitness") or []
            wsize = cs(len(w)) + sum(cs(len(x) // 2) + len(x) // 2 for x in w) if w else 0
            for size in (len(ss), len(spk["hex"]) // 2, wsize):
                if size > big: big, big_in = size, v
            leg += sigops(ss, False)
            prog = bytes.fromhex(spk["hex"])
            if spk["type"] == "scripthash":
                s = ops(ss)[0]
                prog = s[-1][1] if s and s[-1][1] is not None else b""
                p2sh += sigops(prog, True)
            if len(prog) == 22 and prog[:2] == b"\x00\x14":
                wit += 1
            elif len(prog) == 34 and prog[:2] == b"\x00\x20" and w:
                wit += sigops(bytes.fromhex(w[-1]), True)
        out_leg = 0
        for o in tx["vout"]:
            spk = bytes.fromhex(o["scriptPubKey"]["hex"])
            big = max(big, len(spk))
            out_leg += sigops(spk, False)
        accurate = 4 * (leg + p2sh) + wit
        full = 4 * (leg + out_leg + p2sh) + wit
        vsize = (max(tx["weight"], full * 20) + 3) // 4
        bpss = vsize * 4 // accurate + 1 if accurate else None
        s = shown.get(tx["txid"])
        fix = ((s or {}).get("fix") or {}).get("knots") or {}
        rows.append({"h": h, "txid": tx["txid"], "tier": s["tier"] if s else "clean", "inputs": len(tx["vin"]),
                     "fake": bool(s) and s["tier"] == "sewage" and "fake-multisig" in s.get("types", []),
                     "knots": not s or not s["v"]["knots"], "plumb": not s or not s["v"]["plumb"],
                     "knots42": (not s or not s["v"]["knots"]) and fix.get("dcs", -1) < 42,
                     "bpss": bpss, "mss": big - 1, "kind": kind(big_in) if big_in else "output"})
    return [r for r in rows if (r["bpss"] or 999) <= 120 or r["mss"] >= 520 or r["tier"] == "sewage"]


def spread(values):
    """Counts by value, small values first, None last."""
    return sorted(Counter(values).items(), key=lambda kv: (kv[0] is None, kv[0] or 0))


def main():
    lo, hi = int(sys.argv[1]), int(sys.argv[2])
    value = int(sys.argv[3]) if len(sys.argv) > 3 else 51
    mss = int(sys.argv[4]) if len(sys.argv) > 4 else 590
    with Pool(3) as p:
        rows = [r for rs in p.imap(one, range(lo, hi + 1), chunksize=8) for r in rs]
    sewage = [r for r in rows if r["tier"] == "sewage"]
    by_value = [r for r in rows if r["bpss"] is not None and r["bpss"] <= value]
    by_mss = [r for r in rows if r["mss"] >= mss]
    facts = {
        "lo": lo, "hi": hi, "value": value, "mss": mss,
        # sewage Knots relays at the defaults, and at datacarriersize=42
        "knots_relays_sewage": sum(r["knots"] for r in sewage),
        "knots42_relays_sewage": sum(r["knots42"] for r in sewage),
        # what -bytespersigopstrict=value refuses that Knots relays at the defaults
        "value_sewage": sum(r["knots"] for r in by_value if r["tier"] == "sewage"),
        "value_sewage_knots42": sum(r["knots42"] for r in by_value if r["tier"] == "sewage"),
        "value_sewage_plumb_relays": sum(r["plumb"] for r in by_value if r["tier"] == "sewage"),
        "value_other": sum(1 for r in by_value if r["tier"] != "sewage"),
        "value_kinds": Counter(r["kind"] for r in by_value if r["knots"]).most_common(),
        # the sewage the site lists as fake multisig (Plumb's -rejectfakemultisig counts it, or its keys
        # look unused): how many, who relays it, and the smallest -bytespersigopstrict refusing each
        "fake": sum(r["fake"] for r in rows),
        "fake_knots_relays": sum(r["fake"] and r["knots"] for r in rows),
        "fake_knots42_relays": sum(r["fake"] and r["knots42"] for r in rows),
        "fake_plumb_refuses": sum(r["fake"] and not r["plumb"] for r in rows),
        "fake_bpss": spread(r["bpss"] for r in rows if r["fake"]),
        "fake_inputs": spread(min(r["inputs"] // 100 * 100, 400) for r in rows if r["fake"]),
        "fake_largest": spread(r["mss"] + 1 for r in rows if r["fake"]),
        # the smallest value that refuses a transaction not listed as sewage
        "nearest_other": min((r["bpss"] for r in rows if r["tier"] != "sewage" and r["bpss"]), default=None),
        # what -maxscriptsize=mss refuses that Knots relays at the defaults
        "mss_sewage": sum(r["knots"] for r in by_mss if r["tier"] == "sewage"),
        "mss_other": sum(r["knots"] for r in by_mss if r["tier"] != "sewage"),
        "mss_other_kinds": Counter(r["kind"] for r in by_mss if r["knots"] and r["tier"] != "sewage").most_common(),
        # the largest script or witness among transactions not listed as sewage that Knots relays
        "largest_other": max(r["mss"] + 1 for r in rows if r["tier"] != "sewage" and r["knots"]),
    }
    json.dump(facts, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
