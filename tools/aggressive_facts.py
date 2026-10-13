#!/usr/bin/env python3
"""The numbers the aggressive Knots file quotes (site.AGGR_FACTS):
aggressive_facts.py LO HI [DUST] [STRICT], counted over every transaction in the cached blocks.

The recommended Knots file relays a transaction when Knots gives no reason at its defaults, its
fix.knots.dcs is under 42 and its fix.knots.bpss is over 51 (the same test as conf_facts.py and
sigop_facts.py), and its fee meets Knots' minimum relay fee, 1 sat a vbyte, on the vsize Knots gives
it at datacarriercost=2 (validation.cpp PreChecks): weight plus 4 for each byte of data Knots counts
in an OP_RETURN or a scriptSig and 7 for each in a witness, or 20 for each sigop if that is more, in
quarters rounded up. Where the records cannot tell a scriptSig from a witness, the fee is checked at
both ends and floor_unsure counts the ones that differ. Version 3 gets no exemption: Knots waives the
floor for it only at mempooltruc=enforce, and the file leaves mempooltruc at accept.

The aggressive file adds -dustrelayfee=DUST, in sat/kvB (default 5000), and
-bytespersigopstrict=STRICT (default 55):
  DUST refuses a transaction with an output that is dust at DUST and not at Knots' default 3000.
     An output is dust when its value is under GetDustThreshold (src/policy/policy.cpp): the fee at
     that rate, rounded up, for the serialized output plus 67 bytes for a witness program or 148
     for anything else. OP_RETURN outputs never are, and a zero-value pay-to-anchor output is let
     through by permitephemeral=anchor at any rate.
  STRICT refuses a transaction whose smallest refusing -bytespersigopstrict, as sigop_facts.py
     counts it, is STRICT or less.
A Lightning force close is a transaction whose locktime's top byte is 0x20 and every input's
sequence's top byte is 0x80, as BOLT 3 builds a commitment transaction.

Prints, as JSON: txs and sewage in the blocks; ln, the force closes the recommended file relays;
run_all, the p2wsh-run transactions; floor_sewage and floor_other, what passes that file's filters
but not its fee floor; fees, the sats paid by what the aggressive file refuses beyond that file. Of
the sewage that file relays: conf_sewage (conf_sewage_w its weight), left (left_w) what the
aggressive file still relays, dust_sewage what DUST refuses, dust_run
the p2wsh-run among them, strict_sewage what STRICT refuses and DUST does not, strict_fake and
strict_liquid the fake multisig and Liquid-layout spends STRICT refuses, run (run_w) its p2wsh-run,
run_546 those paying 546 sats to a P2WSH output, run546_median the median count of such outputs
among those. Of the rest that file relays: other, strict_other what STRICT refuses, dust_other what
DUST refuses, dust_ln and dust_ln_330 the force closes among them and those a 330-sat output makes
dust, dust_other_kinds the output type and value that make the others dust, most common first.
thresholds: the dust threshold at DUST for each output type."""
import gzip, json, math, os, sys
from collections import Counter
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sigop_facts  # noqa: E402

HOME = sigop_facts.HOME
P2A = "51024e73"


def dust_size(spk):
    """GetDustThreshold's byte count for an output, or None for an OP_RETURN or oversized script."""
    if (spk and spk[0] == 0x6a) or len(spk) > 10000:
        return None
    witness = 4 <= len(spk) <= 42 and (spk[0] == 0 or 0x51 <= spk[0] <= 0x60) and spk[1] + 2 == len(spk)
    return 8 + sigop_facts.cs(len(spk)) + len(spk) + (32 + 4 + 1 + 107 // 4 + 4 if witness else 32 + 4 + 1 + 107 + 4)


def threshold(spk, rate):
    return math.ceil(rate * dust_size(spk) / 1000)


def sigop_cost(tx):
    """GetTransactionSigOpCost at the standard flags, as sigop_facts.py counts its parts."""
    leg = p2sh = wit = 0
    for v in tx["vin"]:
        ss = bytes.fromhex(v["scriptSig"]["hex"])
        spk = v["prevout"]["scriptPubKey"]
        w = v.get("txinwitness") or []
        leg += sigop_facts.sigops(ss, False)
        prog = bytes.fromhex(spk["hex"])
        if spk["type"] == "scripthash":
            pushes = sigop_facts.ops(ss)[0]
            prog = pushes[-1][1] if pushes and pushes[-1][1] is not None else b""
            p2sh += sigop_facts.sigops(prog, True)
        if len(prog) == 22 and prog[:2] == b"\x00\x14":
            wit += 1
        elif len(prog) == 34 and prog[:2] == b"\x00\x20" and w:
            wit += sigop_facts.sigops(bytes.fromhex(w[-1]), True)
    leg += sum(sigop_facts.sigops(bytes.fromhex(o["scriptPubKey"]["hex"]), False) for o in tx["vout"])
    return 4 * (leg + p2sh) + wit


def fee_floor(tx, s):
    """The lowest and highest fee Knots can ask of tx at 1 sat/vB and datacarriercost=2."""
    data = sum(s["dc"]["knots"]) if s else 0
    out = sum(o.get("data", 0) for o in s["outs"] if o["type"] == "nulldata") if s else 0
    extra_lo = 4 * data
    extra_hi = 4 * data if out >= data else 4 * out + 7 * (data - out)
    by_sigops = sigop_cost(tx) * 20
    return tuple(-(-max(tx["weight"] + e, by_sigops) // 4) for e in (extra_lo, extra_hi))


def one(args):
    h, dust = args
    strict = {r["txid"]: r["bpss"] for r in sigop_facts.one(h)}
    with gzip.open(f"{HOME}/raw/{h}.json.gz") as f:
        b = json.load(f)
    with open(f"{HOME}/blocks/{h}.json") as f:
        shown = {s["txid"]: s for s in json.load(f).get("spam", [])}
    rows = []
    for tx in b["tx"][1:]:
        s = shown.get(tx["txid"])
        fx = ((s or {}).get("fix") or {}).get("knots") or {}
        filters = not s or (not s["v"]["knots"] and fx.get("dcs", -1) < 42 and fx.get("bpss", 999) > 51)
        fee = round(tx["fee"] * 1e8)
        lo, hi = fee_floor(tx, s)
        fee_ok = fee >= hi
        unsure = lo <= fee < hi
        relays = filters and fee_ok
        hit = None
        n546 = 0
        for o in tx["vout"]:
            spk, val = bytes.fromhex(o["scriptPubKey"]["hex"]), round(o["value"] * 1e8)
            n546 += o["scriptPubKey"]["type"] == "witness_v0_scripthash" and val == 546
            if (o["scriptPubKey"]["hex"] == P2A and val == 0) or dust_size(spk) is None:
                continue
            if val >= threshold(spk, 3000) and val < threshold(spk, dust) and hit is None:
                hit = f'{o["scriptPubKey"]["type"]}:{val}'
        ln = (tx["locktime"] >> 24) == 0x20 and all((v["sequence"] >> 24) == 0x80 for v in tx["vin"])
        rows.append({"tier": s["tier"] if s else "clean", "types": s["types"] if s else [], "relays": relays,
                     "floor": filters and not fee_ok, "unsure": filters and unsure, "fee": fee,
                     "dust": hit, "strict": strict.get(tx["txid"]), "ln": ln, "n546": n546, "w": tx["weight"]})
    return rows


def main():
    lo, hi = int(sys.argv[1]), int(sys.argv[2])
    dust = int(sys.argv[3]) if len(sys.argv) > 3 else 5000
    value = int(sys.argv[4]) if len(sys.argv) > 4 else 55
    c, kinds, run546 = Counter(), Counter(), []
    with Pool(3) as p:
        for rows in p.imap(one, ((h, dust) for h in range(lo, hi + 1)), chunksize=8):
            for r in rows:
                sewage = r["tier"] == "sewage"
                by_dust = r["dust"] is not None
                by_strict = r["strict"] is not None and r["strict"] <= value
                c["txs"] += 1
                c["sewage"] += sewage
                c["ln"] += r["ln"] and r["relays"]
                c["run_all"] += "p2wsh-run" in r["types"]
                c["floor_sewage" if sewage else "floor_other"] += r["floor"]
                c["floor_unsure"] += r["unsure"]
                if not r["relays"]:
                    continue
                c["fees"] += r["fee"] if by_dust or by_strict else 0
                if sewage:
                    c["conf_sewage"] += 1
                    c["conf_sewage_w"] += r["w"]
                    c["left"] += not by_dust and not by_strict
                    c["left_w"] += r["w"] if not by_dust and not by_strict else 0
                    c["dust_sewage"] += by_dust
                    c["dust_run"] += by_dust and "p2wsh-run" in r["types"]
                    c["strict_sewage"] += by_strict and not by_dust
                    c["strict_fake"] += by_strict and "fake-multisig" in r["types"]
                    c["strict_liquid"] += by_strict and "liquid-emergency" in r["types"]
                    if "p2wsh-run" in r["types"]:
                        c["run"] += 1
                        c["run_w"] += r["w"]
                        c["run_546"] += r["n546"] > 0
                        if r["n546"]:
                            run546.append(r["n546"])
                else:
                    c["other"] += 1
                    c["strict_other"] += by_strict
                    if by_dust:
                        c["dust_other"] += 1
                        c["dust_ln"] += r["ln"]
                        c["dust_ln_330"] += r["ln"] and r["dust"].endswith(":330")
                        if not r["ln"]:
                            kinds[r["dust"]] += 1
    run546.sort()
    sizes = {"p2wsh": "0020" + "00" * 32, "p2tr": "5120" + "00" * 32, "p2wpkh": "0014" + "00" * 20,
             "p2sh": "a914" + "00" * 20 + "87", "p2pkh": "76a914" + "00" * 20 + "88ac"}
    facts = {"lo": lo, "hi": hi, "dust": dust, "value": value, **c,
             "run546_median": run546[len(run546) // 2] if run546 else None,
             "thresholds": {k: threshold(bytes.fromhex(v), dust) for k, v in sizes.items()},
             "dust_other_kinds": kinds.most_common(12)}
    json.dump(facts, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
