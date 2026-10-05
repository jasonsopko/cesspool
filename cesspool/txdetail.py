"""Per-transaction records for the transaction page.

One file per block lists every transaction with what the page draws: amounts,
address types, how each input is spent, witness item sizes, the data bytes the
policy code counted in each input and output, the verdicts with each policy's
data count and the bytes each Plumb filter adds. It never holds
a payload: no witness or script bytes and no OP_RETURN contents. No output of a
type the policy code counted data in, in that transaction, shows its address:
for OLGA and other fake outputs the address is the payload, and the rule also
hides the rest of a run and any real payment of that type.
"""
from . import classify

OP_CHECKSIG, OP_CHECKSIGADD, OP_NUMEQUAL = 0xAC, 0xBA, 0x9C


def sats(btc):
    return round(btc * 1e8)


def multi_a(script_hex):
    """(m, n) for a tapscript `<k> CHECKSIG <k> CHECKSIGADD ... <m> NUMEQUAL`, else None."""
    o = classify.ops(script_hex)
    if len(o) < 4 or o[-1][0] != OP_NUMEQUAL or o[1][0] != OP_CHECKSIG:
        return None
    m = o[-2][0] - 0x50 if classify.OP_1 <= o[-2][0] <= classify.OP_16 else None
    keys = o[:-2]
    if m is None or len(keys) % 2:
        return None
    for k in range(0, len(keys), 2):
        if keys[k][1] is None or len(keys[k][1]) != 32 or keys[k + 1][0] != (OP_CHECKSIG if k == 0 else OP_CHECKSIGADD):
            return None
    return m, len(keys) // 2


def spend(vin, ptype):
    """How an input is spent, in a few words."""
    w = vin.get("txinwitness") or []
    if ptype == "witness_v1_taproot":
        items = w[:-1] if len(w) >= 2 and w[-1].startswith("50") else w
        if len(items) <= 1:
            return "key path"
        leaf, cb = items[-2], items[-1]
        ms = multi_a(leaf)
        depth = max(0, (len(cb) // 2 - 33) // 32)
        what = f"{ms[0]}-of-{ms[1]} multisig" if ms else f"{len(leaf) // 2}-byte script"
        return f"script path, {what}" + (f", {depth} levels deep" if depth else "")
    if ptype == "witness_v0_keyhash":
        return "signature and key"
    if ptype == "witness_v0_scripthash":
        if not w:
            return "script"
        ms = classify.multisig(w[-1])
        return f"{ms[0]}-of-{ms[1]} multisig" if ms else f"{len(w[-1]) // 2}-byte script"
    if ptype == "scripthash":
        if w:
            return "nested segwit, " + ("signature and key" if len(w) == 2 and len(w[-1]) == 66 else "script")
        o = classify.ops(vin.get("scriptSig", {}).get("hex", ""))
        ms = classify.multisig(o[-1][1].hex()) if o and o[-1][1] else None
        return f"{ms[0]}-of-{ms[1]} multisig" if ms else "script"
    if ptype == "pubkeyhash":
        return "signature and key"
    if ptype == "anchor":
        return "anchor, no signature"
    return ptype or "unknown"


def tx_record(t, row, c):
    """The page's view of one transaction. `row` is the plumb-check row, `c` classify's findings."""
    data_in = (row or {}).get("data_in", [])
    data_out = (row or {}).get("data_out", [])
    # Fake multisig inputs show every unused key as payload, not only the ones past Plumb's allowance.
    fm_in = (c or {}).get("fm_in") or []
    ins = []
    for i, v in enumerate(t["vin"]):
        p = v.get("prevout", {})
        spk = p.get("scriptPubKey", {})
        ptype = spk.get("type", "")
        d = max(data_in[i] if i < len(data_in) else 0, fm_in[i] if i < len(fm_in) else 0)
        r = {"p": f'{v["txid"]}:{v["vout"]}', "a": sats(p.get("value", 0)), "t": ptype,
             "ad": spk.get("address"), "sp": spend(v, ptype),
             "ws": [len(x) // 2 for x in v.get("txinwitness") or []]}
        ss = len(v.get("scriptSig", {}).get("hex", "")) // 2
        if ss:
            r["ss"] = ss
        if d:
            r["d"] = d
        ins.append(r)
    # The policy code can book a whole payload on the first output of a run (OLGA does), so
    # every output of a type that carries counted data hides its address, not only that one.
    # That hides real payments of that type in the same transaction too; nothing here can
    # tell them from payload.
    data_types = {o["scriptPubKey"].get("type", "") for i, o in enumerate(t["vout"])
                  if i < len(data_out) and data_out[i] and o["scriptPubKey"].get("type") != "nulldata"}
    token = bool(row and "v" in row and "tokens-json" in row["v"]["plumb"]["reasons"])
    outs = []
    for i, o in enumerate(t["vout"]):
        spk = o["scriptPubKey"]
        ptype = spk.get("type", "")
        d = data_out[i] if i < len(data_out) else 0
        r = {"a": sats(o["value"]), "t": ptype, "n": len(spk["hex"]) // 2}
        if ptype == "nulldata":
            r["l"] = classify.nulldata(t, spk["hex"], token=token)[1]
        elif ptype in data_types:
            # 1: the policy code counted this output as data; 2: another output of its type was
            r["hid"] = 1 if d else 2
        else:
            r["ad"] = spk.get("address")
        if d:
            r["d"] = d
        outs.append(r)
    rec = {"id": t["txid"], "s": t["size"], "vs": t["vsize"], "w": t["weight"],
           "f": sats(t.get("fee", 0)), "i": ins, "o": outs}
    if c:
        rec["k"] = c["tier"]
        if c["types"]:
            rec["ty"], rec["lb"] = c["types"], c["labels"]
        if c["data_bytes"]:
            rec["dt"] = c["data_bytes"]
        if c["missed"]:
            rec["m"] = 1
    if row and "v" in row and any(row["v"][k]["reasons"] for k in ("core", "knots", "plumb")):
        rec["x"] = {k: row["v"][k]["reasons"] for k in ("core", "knots", "plumb")}
    if row and "v" in row:
        # Data bytes each policy counts, [in OP_RETURN, elsewhere], and the bytes each of Plumb's filters adds.
        dc = {k: [row["v"][k]["data"], row["v"][k]["data_nonstd"]] for k in ("knots", "plumb")}
        if any(sum(x) for x in dc.values()):
            rec["dc"] = dc
        if row.get("fc"):
            rec["fc"] = row["fc"]
        # Settings whose value would refuse it, per software that relays it (plumb-check "fix").
        if row.get("fix"):
            rec["fx"] = row["fix"]
    if row is None or "error" in row:
        rec["e"] = 1
    return rec


def coinbase_record(cb):
    outs = []
    for o in cb["vout"]:
        spk = o["scriptPubKey"]
        r = {"a": sats(o["value"]), "t": spk.get("type", ""), "n": len(spk["hex"]) // 2}
        if spk.get("type") == "nulldata":
            r["l"] = "Witness commitment" if spk["hex"].startswith("6a24aa21a9ed") else classify.nulldata(cb, spk["hex"])[1]
        elif spk.get("address"):
            r["ad"] = spk["address"]
        outs.append(r)
    return {"id": cb["txid"], "s": cb["size"], "vs": cb["vsize"], "w": cb["weight"], "cb": 1, "o": outs}
