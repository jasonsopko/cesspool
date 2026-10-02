"""Name what a transaction carries.

The verdicts (would Core, Knots or Plumb relay it, and how many data bytes
Plumb counts where) come from plumb-check, which runs the shipped policy code.
This module only names the protocol and finds the shapes Plumb does not count
yet, so a miss is shown as a miss. It never decodes or keeps the payload
itself beyond the few bytes needed to recognize a protocol.
"""
import re

OP_0, OP_PUSHDATA1, OP_PUSHDATA2, OP_PUSHDATA4 = 0x00, 0x4C, 0x4D, 0x4E
OP_1, OP_16, OP_IF, OP_RETURN, OP_13 = 0x51, 0x60, 0x63, 0x6A, 0x5D
OP_CHECKMULTISIG, OP_CHECKSIG = 0xAE, 0xAC
OP_RESERVED, OP_DROP, OP_2DROP = 0x50, 0x75, 0x6D

SECP_P = 2**256 - 2**32 - 977

# Reasons from the policy code that mean "this carries data", as opposed to
# dust, sizes or versions.
DATA_REASONS = re.compile(r"^(txn-datacarrier-|tokens-|parasite-|bare-datacarrier|multi-op-return|bare-multisig|bad-txns-input-.*datacarrier|bad-witness-.*datacarrier)")

SWAP_MEMO = re.compile(rb"^(=\||=|s|swap|SWAP|\+|a|add|ADD|-|wd|WITHDRAW|withdraw|OUT|REFUND|LOAN\+|LOAN-|\$\+|\$-|n|trade\+|TRADE\+|~|BOND|UNBOND|LEAVE)[:|]")


def ops(hexstr):
    """(opcode, data or None) pairs; stops quietly on a truncated push."""
    b = bytes.fromhex(hexstr)
    i, out = 0, []
    while i < len(b):
        op = b[i]
        i += 1
        if 0 < op < OP_PUSHDATA1:
            n = op
        elif op == OP_PUSHDATA1 and i + 1 <= len(b):
            n, i = b[i], i + 1
        elif op == OP_PUSHDATA2 and i + 2 <= len(b):
            n, i = int.from_bytes(b[i:i + 2], "little"), i + 2
        elif op == OP_PUSHDATA4 and i + 4 <= len(b):
            n, i = int.from_bytes(b[i:i + 4], "little"), i + 4
        else:
            out.append((op, b"" if op == OP_0 else None))
            continue
        out.append((op, b[i:i + n]))
        i += n
    return out


def printable(b):
    return len(b) > 0 and sum(32 <= c < 127 or c in (9, 10, 13) for c in b) / len(b) >= 0.9


def on_curve(x):
    return x < SECP_P and pow((x**3 + 7) % SECP_P, (SECP_P - 1) // 2, SECP_P) == 1


def rc4(key, data):
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray()
    for c in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(c ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


def multisig(script_hex):
    """(m, n) for a plain m-of-n CHECKMULTISIG script, else None."""
    o = ops(script_hex)
    if len(o) < 4 or o[-1][0] != OP_CHECKMULTISIG:
        return None
    m, n = o[0][0], o[-2][0]
    if not (OP_1 <= m <= OP_16 and OP_1 <= n <= OP_16):
        return None
    keys = o[1:-2]
    m, n = m - 0x50, n - 0x50
    if len(keys) != n or any(d is None or len(d) not in (33, 65) for _, d in keys):
        return None
    return m, n


def nulldata(tx, spk_hex):
    """Name an OP_RETURN output."""
    o = ops(spk_hex)
    rest = o[1:]
    if rest and rest[0][0] == OP_13:
        if len(rest) == 1:
            return "runes-empty", "Empty runestone (moves runes, carries no bytes)"
        return "runes", "Runestone (OP_RETURN OP_13)"
    payload = b"".join(d or b"" for _, d in rest)
    if not payload:
        return "opreturn-empty", "Empty OP_RETURN"
    if payload.startswith(b"omni"):
        return "omni", "Omni Layer token message"
    if payload[:2] in (b"X2", b"id") and len(payload) > 2 and payload[2:3] in (b"[", b"^", b"$", b"p", b"x", b"#"):
        return "stacks", "Stacks commitment"
    if payload.startswith(b"CNTRPRTY"):
        return "counterparty", "Counterparty message"
    if tx["vin"] and "txid" in tx["vin"][0]:
        if rc4(bytes.fromhex(tx["vin"][0]["txid"]), payload[:8]) == b"CNTRPRTY":
            return "counterparty", "Counterparty message (RC4 under the first input's txid)"
    if SWAP_MEMO.match(payload):
        return "swap-memo", "Cross-chain swap memo"
    if printable(payload):
        return "opreturn-text", f"Text note, {len(payload)} bytes"
    return "opreturn-data", f"Binary note, {len(payload)} bytes"


def fake_output(tx, idx, vout):
    """Name an output whose hash or key plumb-check counted as data."""
    spk = vout["scriptPubKey"]
    t = spk.get("type", "")
    prog = bytes.fromhex(spk["hex"])[2:]
    if t == "witness_v0_scripthash":
        if prog[2:8] == b"stamp:" or prog[2:6] == b"ACME":
            return "olga", "OLGA payload in P2WSH hashes (Stamps framing)"
        return "p2wsh-run", "Run of dust P2WSH outputs whose hashes are data"
    if t == "witness_v0_keyhash":
        if printable(prog):
            return "swap-memo-hash", "Swap memo continued as text inside P2WPKH hashes"
        return "fake-hash", "Data inside P2WPKH hashes"
    if t == "witness_v1_taproot":
        if printable(prog):
            return "taproot-text", "Text inside taproot output keys"
        if not on_curve(int.from_bytes(prog, "big")):
            return "taproot-offcurve", "Taproot output keys that are not points (data)"
        return "taproot-data", "Data inside taproot output keys"
    return "fake-output", "Data in an output script"


def witness_script(vin):
    """(kind, script hex) for the script a witness input reveals, if any."""
    w = vin.get("txinwitness") or []
    t = vin.get("prevout", {}).get("scriptPubKey", {}).get("type", "")
    if t == "witness_v0_scripthash" and w:
        return "p2wsh", w[-1]
    if t == "witness_v1_taproot" and len(w) >= 2:
        items = w[:-1] if w[-1].startswith("50") and len(w) >= 3 else w
        if len(items) >= 2:
            return "tapscript", items[-2]
    if t == "scripthash":
        o = ops(vin.get("scriptSig", {}).get("hex", ""))
        if o and o[-1][1]:
            return "p2sh", o[-1][1].hex()
    return None, None


def bare_envelope(o):
    """The first push of a run of pushes ended by OP_DROP or OP_2DROP, the shape
    -rejectbareenvelopes counts; None if the script has no such run."""
    run = []
    for op, data in o:
        if op <= OP_16 and op != OP_RESERVED:
            run.append(data or b"")
        elif (op == OP_2DROP and run) or (op == OP_DROP and len(run) >= 2):
            # one push then OP_DROP is the shape Knots already counts
            return run[0]
        else:
            run = []
    return None


def data_input(vin):
    """Name a witness input that plumb-check counted as data."""
    kind, script = witness_script(vin)
    if script:
        o = ops(script)
        for k in range(len(o) - 2):
            if o[k][0] == OP_0 and o[k + 1][0] == OP_IF:
                tag = o[k + 2][1] or b""
                if tag == b"ord":
                    return "inscription", "Ordinals inscription envelope"
                return "envelope", "OP_FALSE OP_IF envelope"
        tag = bare_envelope(o)
        if tag is not None:
            if tag == b"ord":
                return "bare-inscription", "Ordinals inscription in a bare envelope (OP_2DROP, no OP_IF)"
            return "bare-envelope", "Run of data pushes dropped again with OP_DROP or OP_2DROP"
        if kind == "tapscript":
            return "dead-branch", "Data in a script branch that can never run"
    return "witness-data", "Data in an input's witness or script"


def fake_multisig(tx):
    """Inputs revealing m-of-n scripts with eight or more unneeded keys.

    Not counted by Plumb 2 (knots#422 and #435 target it). The threshold keeps
    real vaults out: no 1-of-3 or 2-of-5 spend comes near it.
    """
    per_input = []
    for vin in tx["vin"]:
        kind, script = witness_script(vin)
        ms = multisig(script) if kind in ("p2wsh", "p2sh") else None
        per_input.append((ms[1] - ms[0]) * 33 if ms and ms[1] - ms[0] >= 8 else 0)
    return sum(1 for b in per_input if b), sum(per_input), per_input


def classify(tx, verdict):
    """Findings for one REST transaction and its plumb-check row.

    Returns dict: tier ('clean' | 'gray' | 'sewage'), types (list of type ids,
    first is primary), labels, data_bytes (Plumb's count, or our estimate for
    a shape Plumb misses), missed (True when Plumb relays a known spam type).
    """
    v = verdict["v"]
    plumb_reasons = v["plumb"]["reasons"]
    data_reasons = [r for r in plumb_reasons if DATA_REASONS.match(r)]
    types, labels = [], []

    def add(t, label):
        if t not in types:
            types.append(t)
            labels.append(label)

    for vout in tx["vout"]:
        if vout["scriptPubKey"].get("type") == "nulldata":
            add(*nulldata(tx, vout["scriptPubKey"]["hex"]))
    for i, n in enumerate(verdict.get("data_out", [])):
        if n and tx["vout"][i]["scriptPubKey"].get("type") != "nulldata":
            add(*fake_output(tx, i, tx["vout"][i]))
    for i, n in enumerate(verdict.get("data_in", [])):
        if n:
            add(*data_input(tx["vin"][i]))
    if any(r.startswith("parasite-cat21") for r in plumb_reasons):
        add("cat21", "CAT-21 mint (nLockTime 21)")
    if "bare-multisig" in plumb_reasons:
        add("bare-multisig", "Bare multisig outputs holding data keys")

    data_bytes = v["plumb"]["data"] + v["plumb"]["data_nonstd"]
    fm_inputs, fm_bytes, fm_per_input = fake_multisig(tx)
    missed = False
    if fm_inputs:
        add("fake-multisig", f"Fake multisig keys in {fm_inputs} input{'s' if fm_inputs != 1 else ''}")
        if not data_reasons:
            missed = True
            data_bytes = max(data_bytes, fm_bytes)

    if data_reasons or missed:
        tier = "sewage"
    elif data_bytes or types:
        tier = "gray"
    else:
        tier = "clean"
    return {"tier": tier, "types": types, "labels": labels, "data_bytes": data_bytes,
            "missed": missed, "reasons": v, "miss_in": fm_per_input if missed else None}
