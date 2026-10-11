#!/usr/bin/env python3
"""Count the numbers the recommended bitcoin.conf files quote (site.CONF_FACTS).

conf_facts.py LO HI

Reads txd/<h>.json for each height from LO to HI, one block at a time. A profile (knots or plumb)
refuses a transaction at its defaults when it gives a reason, and at datacarriersize=42 when it
refuses at its defaults or its fix.<profile>.dcs, the largest datacarriersize that refuses it, is
42 or more. "<profile>42_extra" counts the transactions 42 refuses beyond the defaults that are not
sewage; "extra_both" those both profiles count, "extra_opreturn" and "extra_one_opreturn" those with
an OP_RETURN output and with exactly one, and "extra_text", "extra_binary", "extra_swapmemo" and
"extra_stacks" those whose OP_RETURN carries that label (classify.nulldata).
Prints the counts as JSON.
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cesspool import process  # noqa: E402


def main():
    lo, hi = int(sys.argv[1]), int(sys.argv[2])
    c = Counter()
    for h in range(lo, hi + 1):
        with open(f"{process.TXD}/{h}.json") as f:
            txs = json.load(f)["tx"]
        for r in txs:
            c["txs"] += 1
            x, fx = r.get("x") or {}, r.get("fx", {})
            sewage = r.get("k") == "sewage"
            c["sewage"] += sewage
            extra = []
            for p in ("knots", "plumb"):
                default = bool(x.get(p))
                at42 = default or fx.get(p, {}).get("dcs", -1) >= 42
                c[f"{p}_sewage"] += default and sewage
                c[f"{p}42_sewage"] += at42 and sewage
                if at42 and not default and not sewage:
                    extra.append(p)
                    c[f"{p}42_extra"] += 1
            if extra:
                labels = [o.get("l", "") for o in r["o"] if o.get("t") == "nulldata"]
                c["extra_opreturn"] += bool(labels)
                c["extra_swapmemo"] += any(l.startswith("Cross-chain swap memo") for l in labels)
                c["extra_stacks"] += any(l.startswith("Stacks commitment") for l in labels)
                c["extra_text"] += any(l.startswith("Text note") for l in labels)
                c["extra_binary"] += any(l.startswith("Binary note") for l in labels)
                c["extra_one_opreturn"] += len(labels) == 1
                c["extra_both"] += len(extra) == 2
    print(json.dumps({"lo": lo, "hi": hi, **c}, sort_keys=True))


if __name__ == "__main__":
    main()
