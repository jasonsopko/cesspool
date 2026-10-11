"""Your Blocks (/found/): the indexes that let a miner or a pool find the blocks they mined, by
payout address, pool name or coinbase text. Everything is static JSON under <out>/d/, read by
site.js; site.py writes the page and calls publish() on every build.

  found.json.gz   every pool name, coinbase tag and DATUM gateway tag with its block heights
                  (sorted, delta-encoded), the grade labels and the letter thresholds
  s/<k>.json.gz   one summary per block for heights k*1000 to k*1000+999, naming its pool
  a/<xx>.json.gz  payout address -> [[height, sats], ...], in 256 shards picked by an FNV-1a hash
                  of the address, so a lookup fetches one small file and the server log holds the
                  shard name, not the address

State: <HOME>/found.json keeps each block's coinbase tag and paying outputs, read from the block
record and the transaction detail once per record and again when the record's mtime changes, as
after a reorg, so a build does not reopen fifteen thousand files."""
import collections, gzip, json, os, statistics

from . import miner

HOME = os.environ.get("CESSPOOL_HOME", os.path.expanduser("~/.cesspool"))
BLOCKS = f"{HOME}/blocks"
TXD = f"{HOME}/txd"
STATE = f"{HOME}/found.json"
CHUNK = 1000
LAST = 20  # coinbases the payout reading uses, as reorg.watch reads a pool's last 20


def shard(addr):
    """Two hex characters: the low byte of the 32-bit FNV-1a hash of the address. site.js has the same function."""
    h = 0x811c9dc5
    for c in addr.encode():
        h = ((h ^ c) * 0x01000193) & 0xffffffff
    return f"{h & 0xff:02x}"


def deltas(heights):
    out, prev = [], 0
    for h in sorted(set(heights)):
        out.append(h - prev)
        prev = h
    return out


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def refresh_state(idx):
    """One entry per block in the index: the record's mtime stamp, its coinbase tag and its paying outputs."""
    state = load_state()
    changed = False
    for h, b in idx.items():
        k = str(h)
        cur = state.get(k)
        if cur is not None and cur.get("m") == b.get("_m"):
            continue
        try:
            with open(f"{BLOCKS}/{h}.json") as f:
                tag = json.load(f).get("tag") or ""
            with open(f"{TXD}/{h}.json") as f:
                cb = json.load(f).get("cb") or {}
        except (OSError, ValueError):
            continue  # a record missing or mid-write: the next build picks it up
        outs = [[o["ad"], o["a"]] for o in cb.get("o", []) if o.get("ad") and o.get("a", 0) > 0]
        state[k] = {"m": b.get("_m"), "tag": tag, "o": outs}
        changed = True
    for k in [k for k in state if int(k) not in idx]:
        del state[k]
        changed = True
    if changed:
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            f.write(json.dumps(state, separators=(",", ":")))
        os.replace(tmp, STATE)
    return state


def write_gz_if_changed(path, obj):
    """Write only when the content differs, so a minute's build touches a handful of files."""
    data = json.dumps(obj, separators=(",", ":")).encode()
    try:
        with gzip.open(path) as f:
            if f.read() == data:
                return False
    except (OSError, EOFError):
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wb", compresslevel=6) as f:
        f.write(data)
    os.replace(tmp, path)
    return True


def payout_key(heights, state, kind, own):
    """reorg.watch's reading of how a name's reward leaves its coinbases, from its last LAST blocks,
    as the class key alone: coinbase, finder, held, elsewhere, own, one or none."""
    counts, by_addr = [], collections.Counter()
    for h in sorted(heights)[-LAST:]:
        st = state.get(str(h))
        if not st:
            continue
        counts.append(len(st["o"]))
        for a, s in st["o"]:
            by_addr[a] += s
    total = sum(by_addr.values())
    if not counts or not total:
        return "none"
    med = round(statistics.median(counts))
    top_addr, top = by_addr.most_common(1)[0]
    dom = 100.0 * top / total
    own_t = None
    if kind == "pool" and own:
        own_t = (top_addr in own, 100.0 * sum(by_addr[a] for a in own) / total)
    return miner.rw().payout_class(med, dom, kind, own_t)[0]


def publish(out, idx, pool_addresses, grades, letters):
    """Write every index for the blocks in idx. pool_addresses: name -> payout addresses on file for it;
    grades: grade key -> label; letters: [[share below which, letter, text], ...], the last with None."""
    state = refresh_state(idx)
    kinds = miner.survey_kinds()
    pools = sorted({b["pool"] for b in idx.values()})
    by_pool, tags, dtags = (collections.defaultdict(list) for _ in range(3))
    chunks = collections.defaultdict(dict)
    shards = {f"{i:02x}": collections.defaultdict(dict) for i in range(256)}
    for h, b in idx.items():
        by_pool[b["pool"]].append(h)
        if b.get("dtag"):
            dtags[b["dtag"]].append(h)
        chunks[h // CHUNK][str(h)] = [b["t"], b["grade"], b["sn"], b["w"], b["sw"], b["pool"], 1 if b.get("via") else 0]
        st = state.get(str(h))
        if not st:
            continue
        if st["tag"]:
            tags[st["tag"]].append(h)
        for a, s in st["o"]:
            paid = shards[shard(a)][a]
            paid[h] = paid.get(h, 0) + s
    doc = {
        "grades": grades, "letters": letters,
        "pools": [[name, miner.slug(name), payout_key(by_pool[name], state, kinds.get(name), pool_addresses.get(name)),
                   deltas(by_pool[name])] for name in pools],
        "tags": [[t, deltas(hs)] for t, hs in sorted(tags.items())],
        "dtags": [[t, deltas(hs)] for t, hs in sorted(dtags.items())],
    }
    write_gz_if_changed(f"{out}/d/found.json.gz", doc)
    for k, d in chunks.items():
        write_gz_if_changed(f"{out}/d/s/{k}.json.gz", d)
    sdir = f"{out}/d/s"
    for name in os.listdir(sdir):
        # a chunk whose whole range left the index: a reorg across a thousand boundary
        if name.endswith(".json.gz") and name[:-8].isdigit() and int(name[:-8]) not in chunks:
            os.remove(f"{sdir}/{name}")
    for s, d in shards.items():
        write_gz_if_changed(f"{out}/d/a/{s}.json.gz", {a: sorted([h, v] for h, v in paid.items()) for a, paid in sorted(d.items())})
