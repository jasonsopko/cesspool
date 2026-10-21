"""Address Lookup (/address/): every sewage transaction since the fork an address took part in, and how.
Static JSON under <out>/d/w/, read by site.js; site.py calls publish() on every build.

  w/<xx>.json.gz  address -> [[height, time, txid, roles, type], ...], in the same 256 shards as Your
                  Blocks' payout index (found.shard), so a lookup fetches one file from each and the
                  server log holds the shard name, not the address

Roles, a bit field per address and transaction:
  SENT   the transaction spends a coin held at the address
  RUNES  the transaction's runestone directs runes to it: an edict names its output, or its output is
         the one that gets whatever runes are left over (the pointer's, else the first output that is not
         OP_RETURN). Whether any arrive depends on what the inputs held, or for a mint or a new rune on
         whether it succeeded, which this site does not track.
  XCP    a Counterparty send in the transaction names it as the receiver
  BTC    an output pays it and none of the above holds

Outputs that carry the data themselves (the policy code counted data bytes in them) are left out: they
are not anyone's wallet. So are pay-to-anchor inputs and outputs, which anyone can spend. Runestones and
Counterparty enhanced sends are read to find who receives; nothing else they carry is shown.

State: <HOME>/address.json keeps each block's record mtime and the shards its rows went to, and each
shard's counts for the page, so a build rewrites only the shards that new or changed blocks touch."""
import collections, gzip, hashlib, json, os, tempfile, zlib

from . import found
from .classify import ops, rc4

HOME = os.environ.get("CESSPOOL_HOME", os.path.expanduser("~/.cesspool"))
BLOCKS = f"{HOME}/blocks"
RAW = f"{HOME}/raw"
STATE = f"{HOME}/address.json"
VERSION = 1  # bump when the rows change: the next build writes every shard again
REBUILD_OVER = 500  # more blocks to add or drop than this: write every shard from scratch
SENT, RUNES, BTC, XCP = 1, 2, 4, 8
TOKENS = RUNES | XCP
READ_ERRORS = (OSError, EOFError, ValueError, zlib.error)  # a file missing, mid-write or corrupt

# ---------------------------------------------------------------- addresses

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def base58check(payload):
    data = payload + hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    n, s = int.from_bytes(data, "big"), ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + s


def _polymod(values):
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i, g in enumerate((0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)):
            if (b >> i) & 1:
                chk ^= g
    return chk


def segwit_address(version, program):
    """Mainnet bech32 (version 0) or bech32m address, or None for a program BIP141 does not allow."""
    if not 0 <= version <= 16 or not 2 <= len(program) <= 40 or (version == 0 and len(program) not in (20, 32)):
        return None
    acc, bits, data = 0, 0, [version]
    for byte in program:
        acc, bits = acc << 8 | byte, bits + 8
        while bits >= 5:
            bits -= 5
            data.append(acc >> bits & 31)
    if bits:
        data.append(acc << (5 - bits) & 31)
    pm = _polymod([3, 3, 0, 2, 3] + data + [0] * 6) ^ (1 if version == 0 else 0x2bc830a3)  # "bc" expanded
    return "bc1" + "".join(BECH32[d] for d in data + [pm >> 5 * (5 - i) & 31 for i in range(6)])


# ---------------------------------------------------------------- runestones

def _pushes(script):
    """The data pushes after a runestone's OP_RETURN OP_13, joined, or None when anything after them is not
    a push or a push runs past the end (ord: Flaw::Opcode, Flaw::InvalidScript)."""
    i, out = 2, []
    while i < len(script):
        op = script[i]
        i += 1
        if op <= 0x4b:
            n = op
        elif op in (0x4c, 0x4d, 0x4e):
            w = {0x4c: 1, 0x4d: 2, 0x4e: 4}[op]
            if i + w > len(script):
                return None
            n, i = int.from_bytes(script[i:i + w], "little"), i + w
        else:
            return None
        if i + n > len(script):
            return None
        out.append(script[i:i + n])
        i += n
    return b"".join(out)


def _varints(b):
    """ord's LEB128 integers, or None for an unterminated, overlong or overflowing one."""
    out, i = [], 0
    while i < len(b):
        n = 0
        for k in range(19):
            if i + k >= len(b):
                return None
            v = b[i + k] & 0x7f
            if k == 18 and v & 0x7c:
                return None
            n |= v << 7 * k
            if not b[i + k] & 0x80:
                break
        else:
            return None
        out.append(n)
        i += k + 1
    return out


def runestone_targets(scripts, self_id=None):
    """Indexes of the outputs a transaction's runestone directs runes to, read as ord's Runestone::decipher
    and rune updater read it, given every output script as bytes and, when known, the transaction's own
    rune id (block height, index in the block). None when there is no runestone; empty for a cenotaph,
    which burns every rune it touches."""
    for s in scripts:
        if s[:2] == b"\x6a\x5d":
            break
    else:
        return None
    n = len(scripts)
    payload = _pushes(s)
    ints = None if payload is None else _varints(payload)
    if ints is None:
        return set()
    fields, edicts, flaw = {}, [], False
    i = 0
    while i < len(ints):
        if ints[i] == 0:  # body: edicts, four integers each, rune ids delta-encoded
            block = tx = 0
            body = ints[i + 1:]
            for q in range(0, len(body), 4):
                db, dt, amount, output = body[q:q + 4] + [None] * (4 - len(body[q:q + 4]))
                if output is None:
                    flaw = True
                    break
                nb = block + db
                nt = tx + dt if db == 0 else dt
                if db >= 2**64 or nb >= 2**64 or dt >= 2**32 or nt >= 2**32 or (nb == 0 and nt > 0):
                    flaw = True
                    break
                if output > n:  # n itself means every output that is not OP_RETURN
                    flaw = True
                    break
                block, tx = nb, nt
                edicts.append((block, tx, amount, output))
            break
        if i + 1 >= len(ints):
            flaw = True
            break
        fields.setdefault(ints[i], []).append(ints[i + 1])
        i += 2

    def take(tag, k=1, ok=lambda *v: True):
        """ord's Tag::take: the first k values, removed only when there are k and they are acceptable."""
        vals = fields.get(tag, [])
        if len(vals) < k or not ok(*vals[:k]):
            return None
        got = vals[:k]
        del vals[:k]
        if not vals:
            del fields[tag]
        return got

    flags = (take(2) or [0])[0]
    etching = bool(flags & 1)
    flags &= ~1
    premine = 0
    if etching:
        take(1, ok=lambda d: d <= 38)  # divisibility
        premine = (take(6) or [0])[0]
        take(4)  # rune
        take(3, ok=lambda s: s <= 0x07ffffff)  # spacers
        take(5, ok=lambda c: c <= 0x10ffff and not 0xd800 <= c <= 0xdfff)  # symbol, a char
        cap = amount = 0
        if flags & 2:  # terms
            flags &= ~2
            cap = (take(8) or [0])[0]
            amount = (take(10) or [0])[0]
            for tag in (12, 14, 16, 18):  # height and offset bounds, u64
                take(tag, ok=lambda h: h < 2**64)
        flags &= ~4  # turbo
        if cap * amount >= 2**128 or premine + cap * amount >= 2**128:
            flaw = True
    take(20, 2, ok=lambda b, t: b < 2**64 and t < 2**32 and not (b == 0 and t > 0))  # mint
    pointer = take(22, ok=lambda p: p < 2**32 and p < n)
    if flaw or flags or any(t % 2 == 0 for t in fields):
        return set()
    op_return = {j for j, x in enumerate(scripts) if x[:1] == b"\x6a"}
    dest = [j for j in range(n) if j not in op_return]
    to, every, prefix = set(), False, 0
    for block, tx, amount, output in edicts:
        if (block, tx) not in ((0, 0), self_id):
            # Any other rune comes from the inputs or a mint, which this site does not track: count the output.
            if output == n:
                every = True
            else:
                to.add(output)
            continue
        # 0:0, or the transaction's own id, is the rune it etches. ord skips the edict when nothing is
        # etched (whether an etching succeeds depends on chain state, like input balances), and the etched
        # rune's only balance is the premine, handed out in turn the way rune_updater does: amount 0 takes
        # the rest, output n splits across dest with the remainder to the first ones, and a zero share is
        # no share.
        if not premine:
            continue
        if output < n:
            to.add(output)
            premine -= premine if amount == 0 else min(amount, premine)
        elif dest:
            if amount == 0:
                share, rest = divmod(premine, len(dest))
                prefix = max(prefix, len(dest) if share else rest)
                premine = 0
            else:
                prefix = max(prefix, min(len(dest), -(-premine // amount)))
                premine -= min(premine, amount * len(dest))
    if every:
        to.update(dest)
    to.update(dest[:prefix])
    left = pointer[0] if pointer else (dest[0] if dest else None)
    if left is not None:
        to.add(left)
    return to - op_return


# ---------------------------------------------------------------- Counterparty

CP_PREFIX = b"CNTRPRTY"
CP_ENHANCED_SEND = 2
CBOR_TAGS = {28, 256, 258, 55799}  # tags cbor2 unwraps around an array or map: shareable, stringref namespace, set, self-describe


def _cbor_send(b):
    """[asset id, quantity, address bytes, memo bytes] from a CBOR array, as counterparty-core packs an
    enhanced send since its taproot_support change (mainnet block 902000), or None. Integers and byte
    strings only: other CBOR forms cbor2 would also accept, such as a text memo, are not read."""
    i = 0

    def head():
        nonlocal i
        if i >= len(b):
            raise ValueError
        major, info = b[i] >> 5, b[i] & 31
        i += 1
        if info < 24:
            return major, info
        w = {24: 1, 25: 2, 26: 4, 27: 8}.get(info)
        if w is None or i + w > len(b):
            raise ValueError
        i += w
        return major, int.from_bytes(b[i - w:i], "big")

    try:
        if head() != (4, 4):
            return None
        out = []
        for want in (0, 0, 2, 2):
            major, v = head()
            if major != want:
                return None
            if major == 2:
                if i + v > len(b):
                    return None
                out.append(b[i:i + v])
                i += v
            else:
                out.append(v)
        return out
    except ValueError:
        return None


def _legacy_send(b):
    """The same four from the older fixed layout: asset id, quantity, 21 address bytes, memo of up to 34."""
    if not 37 <= len(b) <= 37 + 34:
        return None
    return [int.from_bytes(b[:8], "big"), int.from_bytes(b[8:16], "big"), b[16:37], b[37:]]


def _cp_address(b):
    """(accepted, address or None) for Counterparty's packed address, unpacked the way its address.unpack
    does it: 0x01 P2PKH, 0x02 P2SH, 0x03 witness version + program; failing those, the legacy packing,
    0x80 + witness version or a base58 version byte. Legacy unpacking turns other bytes into a string that
    is no mainnet address; Counterparty accepts the send and credits that string, so there is no address
    to list. Not accepted means Counterparty raises, and moves on to the older message layout."""
    if len(b) == 21 and b[0] in (1, 2):
        return True, base58check(bytes([0x00 if b[0] == 1 else 0x05]) + b[1:])
    if len(b) >= 22 and b[0] == 3:
        a = segwit_address(b[1], b[2:])
        if a:
            return True, a
    if not b:
        return False, None
    if b[0] == 0x80:
        a = segwit_address(0, b[1:])
        return (True, a) if a else (False, None)
    if 0x81 <= b[0] <= 0x8f:
        # python-bitcoinlib, which counterparty-core pins, writes the original bech32 checksum for every
        # version: a string for any 2 to 40 byte program, but no address that version 1 and up use.
        return 2 <= len(b) - 1 <= 40, None
    return True, (base58check(b) if len(b) == 21 and b[0] in (0x00, 0x05) else None)


def counterparty_receiver(tx):
    """The address an enhanced send in this REST transaction names as its receiver, or None. The message
    is the first OP_RETURN whose data reads CNTRPRTY after RC4 under the first input's txid; a zero first
    byte after that means a four-byte type id. The body is read as CBOR first and as the older fixed
    layout when that raises, as Counterparty's enhancedsend.unpack does."""
    if not tx["vin"] or "txid" not in tx["vin"][0]:
        return None
    key = bytes.fromhex(tx["vin"][0]["txid"])
    for o in tx["vout"]:
        h = o["scriptPubKey"]["hex"]
        if not h.startswith("6a"):
            continue
        m = rc4(key, b"".join(d or b"" for _, d in ops(h)[1:]))
        if m.startswith(CP_PREFIX):
            break
    else:
        return None
    m = m[len(CP_PREFIX):]
    if len(m) > 1 and m[0]:
        kind, body = m[0], m[1:]
    elif len(m) > 4:
        kind, body = int.from_bytes(m[:4], "big"), m[4:]
    else:
        return None
    if kind != CP_ENHANCED_SEND:
        return None
    # A body that opens like a CBOR array or map, or with a tag cbor2 unwraps around one (CBOR_TAGS, in any
    # of the head lengths cbor2 accepts), may be one cbor2 accepts where this reader does not, so the older
    # layout is not tried on it: better to name no receiver than one Counterparty would not. No real
    # older-layout send opens that way.
    w = {0xd8: 1, 0xd9: 2, 0xda: 4, 0xdb: 8}.get(body[0]) if body else None
    tagged = w is not None and len(body) > w and int.from_bytes(body[1:1 + w], "big") in CBOR_TAGS
    cborish = body[:1] and 0x80 <= body[0] <= 0xbf or tagged
    sends = [_cbor_send(body)] if cborish else [_cbor_send(body), _legacy_send(body)]
    for send in sends:
        # Asset 0 is BTC and ids from 2 up to 26^3 name no asset: Counterparty raises on both.
        if send and (send[0] == 1 or 26**3 <= send[0] < 2**64):
            ok, a = _cp_address(send[2])
            if ok:
                return a
    return None


# ---------------------------------------------------------------- rows

def tx_roles(s, raw_tx, self_id=None):
    """{address: roles} for one sewage entry of a block record and its REST transaction (None to skip the
    token reading); self_id is the transaction's rune id, (height, index in the block)."""
    roles = collections.defaultdict(int)
    for i in s["ins"]:
        if i.get("addr") and i.get("type") != "anchor":
            roles[i["addr"]] |= SENT
    wallet = {j: o["addr"] for j, o in enumerate(s["outs"]) if o.get("addr") and not o.get("data") and o.get("type") != "anchor"}
    if raw_tx is not None:
        if {"runes", "runes-empty"} & set(s["types"]):
            for j in runestone_targets([bytes.fromhex(o["scriptPubKey"]["hex"]) for o in raw_tx["vout"]], self_id) or ():
                if j in wallet:
                    roles[wallet[j]] |= RUNES
        if "counterparty" in s["types"]:
            a = counterparty_receiver(raw_tx)
            if a:
                roles[a] |= XCP
    for a in wallet.values():
        if not roles.get(a):
            roles[a] = BTC
    return roles


def block_rows(h):
    """[(address, [height, time, txid, roles, type]), ...] for the sewage in block h's record."""
    with open(f"{BLOCKS}/{h}.json") as f:
        rec = json.load(f)
    sewage = [s for s in rec["spam"] if s["tier"] == "sewage"]
    raw = {}
    if any({"runes", "runes-empty", "counterparty"} & set(s["types"]) for s in sewage):
        with gzip.open(f"{RAW}/{h}.json.gz") as f:
            raw = {t["txid"]: (i, t) for i, t in enumerate(json.load(f)["tx"])}
    out = []
    for s in sewage:
        i, t = raw.get(s["txid"], (None, None))
        for a, r in tx_roles(s, t, (h, i) if t else None).items():
            out.append((a, [h, rec["t"], s["txid"], r, s["types"][0] if s["types"] else ""]))
    return out


# ---------------------------------------------------------------- publishing

def shard_counts(d):
    """[addresses a token was directed to, how many of those got one in a transaction that also spent
    from them] for one shard."""
    tok = own = 0
    for rows in d.values():
        if any(x[3] & TOKENS for x in rows):
            tok += 1
            own += any(x[3] & TOKENS and x[3] & SENT for x in rows)
    return [tok, own]


def load_state():
    try:
        with open(STATE) as f:
            st = json.load(f)
        if st.get("v") == VERSION:
            return st
    except (OSError, ValueError):
        pass
    return None


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        f.write(json.dumps(st, separators=(",", ":")))
    os.replace(tmp, STATE)


def read_shard(out, s):
    """A shard's rows; {} when it does not exist yet, None when it cannot be read."""
    try:
        with gzip.open(f"{out}/d/w/{s}.json.gz") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except READ_ERRORS:
        return None


def write_shard(out, s, d):
    for rows in d.values():
        rows.sort(key=lambda x: (x[0], x[2]))
    found.write_gz_if_changed(f"{out}/d/w/{s}.json.gz", dict(sorted(d.items())))


def rebuild(out, idx):
    """Every shard from scratch, through 16 temporary files to keep memory small."""
    st = {"v": VERSION, "b": {}, "s": {}}
    with tempfile.TemporaryDirectory(dir=HOME) as tmp:
        files = {c: open(f"{tmp}/{c}", "w") for c in "0123456789abcdef"}
        for h in sorted(idx):
            try:
                rows = block_rows(h)
            except READ_ERRORS:
                continue  # a record or raw block missing or mid-write: the next build picks it up
            shards = set()
            for a, row in rows:
                s = found.shard(a)
                shards.add(s)
                files[s[0]].write(json.dumps([s, a, row], separators=(",", ":")) + "\n")
            st["b"][str(h)] = [idx[h].get("_m"), sorted(shards)]
        for f in files.values():
            f.close()
        for c in "0123456789abcdef":
            by = {f"{c}{x}": collections.defaultdict(list) for x in "0123456789abcdef"}
            with open(f"{tmp}/{c}") as f:
                for line in f:
                    s, a, row = json.loads(line)
                    by[s][a].append(row)
            for s, d in by.items():
                write_shard(out, s, d)
                st["s"][s] = shard_counts(d)
    save_state(st)
    return st


def publish(out, idx):
    """Bring the shards in line with the blocks in idx. Returns [addresses a token was directed to, how many
    of those got one in a transaction that also spent from them], over every block."""
    st = load_state() if os.path.isdir(f"{out}/d/w") else None
    if st is None:
        st = rebuild(out, idx)
    else:
        stale = {h for h in idx if (st["b"].get(str(h)) or [None])[0] != idx[h].get("_m")}
        gone = {int(k) for k in st["b"] if int(k) not in idx}
        if len(stale) + len(gone) > REBUILD_OVER:
            st = rebuild(out, idx)
        elif stale or gone:
            drop = stale | gone
            touched = set()
            for h in drop:
                touched.update((st["b"].get(str(h)) or [None, []])[1])
            add, new_b = collections.defaultdict(list), {}
            for h in sorted(stale):
                try:
                    rows = block_rows(h)
                except READ_ERRORS:
                    continue  # dropped from the state below, so the next build tries it again
                shards = set()
                for a, row in rows:
                    s = found.shard(a)
                    shards.add(s)
                    add[s].append((a, row))
                new_b[str(h)] = [idx[h].get("_m"), sorted(shards)]
                touched |= shards
            for s in sorted(touched):
                cur = read_shard(out, s)
                if cur is None:
                    st = None  # a shard that cannot be read: write every shard again
                    break
                d = collections.defaultdict(list)
                for a, rows in cur.items():
                    keep = [r for r in rows if r[0] not in drop]
                    if keep:
                        d[a] = keep
                for a, row in add.get(s, []):
                    d[a].append(row)
                write_shard(out, s, d)
                st["s"][s] = shard_counts(d)
            if st is None:
                st = rebuild(out, idx)
            else:
                for h in drop:
                    st["b"].pop(str(h), None)
                st["b"].update(new_b)
                save_state(st)
    tok = sum(c[0] for c in st["s"].values())
    own = sum(c[1] for c in st["s"].values())
    return [tok, own]
