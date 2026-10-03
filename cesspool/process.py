"""Turn cached REST blocks into block records.

A record keeps what the site shows: totals, a compact row per transaction for
the block map, and full findings for every transaction that is not clean.
"""
import gzip, json, os, subprocess, tempfile

from . import classify, miner, txdetail

HOME = os.environ.get("CESSPOOL_HOME", os.path.expanduser("~/.cesspool"))
RAW = f"{HOME}/raw"
BLOCKS = f"{HOME}/blocks"
TXD = f"{HOME}/txd"
CHECKER = os.environ.get("PLUMB_CHECK_BIN", f"{HOME}/bin/test_bitcoin-plumb")
TIERS = {"clean": 0, "gray": 1, "sewage": 2}


def sats(btc):
    return round(btc * 1e8)


def load_raw(h):
    with gzip.open(f"{RAW}/{h}.json.gz") as f:
        return json.load(f)


def run_checker(txs):
    """plumb-check rows keyed by txid, one test_bitcoin run for all txs given."""
    with tempfile.TemporaryDirectory(dir=HOME) as d:
        inp, out = f"{d}/in.txt", f"{d}/out.txt"
        with open(inp, "w") as f:
            for t in txs:
                prev = ",".join(f'{v["prevout"]["scriptPubKey"]["hex"]}:{sats(v["prevout"]["value"])}' for v in t["vin"])
                f.write(f'{t["txid"]} {t["hex"]} {prev}\n')
        env = dict(os.environ, PLUMB_CHECK_IN=inp, PLUMB_CHECK_OUT=out)
        r = subprocess.run([CHECKER, "--run_test=plumb_check_tests", "--log_level=error"], env=env,
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"plumb-check failed: {r.stdout[-2000:]} {r.stderr[-2000:]}")
        rows = {}
        with open(out) as f:
            for line in f:
                row = json.loads(line)
                rows[row["txid"]] = row
        return rows


def tx_detail(t, f, fm_in=None):
    """The parts of a non-clean transaction the dissection page draws."""
    ins = []
    for i, v in enumerate(t["vin"]):
        p = v.get("prevout", {})
        spk = p.get("scriptPubKey", {})
        wit = v.get("txinwitness") or []
        ins.append({"type": spk.get("type", ""), "addr": spk.get("address"), "sats": sats(p.get("value", 0)),
                    "wbytes": sum(len(x) // 2 for x in wit),
                    "data": max(f["data_in"][i] if i < len(f["data_in"]) else 0, fm_in[i] if fm_in else 0)})
    outs = []
    for i, o in enumerate(t["vout"]):
        spk = o["scriptPubKey"]
        outs.append({"type": spk.get("type", ""), "addr": spk.get("address"), "sats": sats(o["value"]),
                     "len": len(spk["hex"]) // 2,
                     "data": f["data_out"][i] if i < len(f["data_out"]) else 0})
    return {"ins": ins, "outs": outs}


def process_block(b, rows):
    """(transaction file, block record) for one block."""
    txs = b["tx"]
    cb = txs[0]
    reward = sum(sats(o["value"]) for o in cb["vout"])
    total_fee, total_w = 0, 0
    tmap, spam, txd = [], [], []
    for t in txs[1:]:
        fee = sats(t.get("fee", 0))
        total_fee += fee
        total_w += t["weight"]
        row = rows.get(t["txid"])
        if row is None or "error" in row:
            tmap.append([t["vsize"], fee, 0, -1])
            txd.append(txdetail.tx_record(t, row, None))
            continue
        c = classify.classify(t, row)
        txd.append(txdetail.tx_record(t, row, c))
        tier = TIERS[c["tier"]]
        if tier:
            d = tx_detail(t, {"data_in": row.get("data_in", []), "data_out": row.get("data_out", [])}, c.get("fm_in"))
            spam.append({"txid": t["txid"], "tier": c["tier"], "types": c["types"], "labels": c["labels"],
                         "data": c["data_bytes"], "missed": c["missed"], "w": t["weight"], "vsize": t["vsize"], "size": t["size"],
                         "fee": fee, "v": {k: c["reasons"][k]["reasons"] for k in ("core", "knots", "plumb")},
                         **d})
            tmap.append([t["vsize"], fee, tier, len(spam) - 1])
        else:
            tmap.append([t["vsize"], fee, 0, -1])
    pool, tag = miner.identify(cb)
    txd = {"h": b["height"], "hash": b["hash"], "t": b["time"], "pool": pool, "ps": miner.slug(pool),
           "cb": txdetail.coinbase_record(cb), "tx": txd}
    sewage = [s for s in spam if s["tier"] == "sewage"]
    gray = [s for s in spam if s["tier"] == "gray"]
    return txd, {
        "h": b["height"], "hash": b["hash"], "prev": b.get("previousblockhash"), "t": b["time"],
        "pool": pool, "tag": tag, "ntx": len(txs), "w": b["weight"],
        "size": b["size"], "reward": reward, "fees": total_fee, "txw": total_w,
        "sewage": {"n": len(sewage), "w": sum(s["w"] for s in sewage), "fee": sum(s["fee"] for s in sewage),
                   "data": sum(s["data"] for s in sewage), "missed": sum(1 for s in sewage if s["missed"])},
        "gray": {"n": len(gray), "w": sum(s["w"] for s in gray), "fee": sum(s["fee"] for s in gray),
                 "data": sum(s["data"] for s in gray)},
        "map": tmap, "spam": spam,
    }


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(json.dumps(obj, separators=(",", ":")))  # dumps uses the C encoder; dump to a file does not
    os.replace(tmp, path)


def process_range(heights):
    os.makedirs(BLOCKS, exist_ok=True)
    os.makedirs(TXD, exist_ok=True)
    blocks = [load_raw(h) for h in heights]
    rows = run_checker([t for b in blocks for t in b["tx"][1:]])
    out = []
    for b in blocks:
        txd, rec = process_block(b, rows)
        write_json(f"{TXD}/{rec['h']}.json", txd)
        write_json(f"{BLOCKS}/{rec['h']}.json", rec)
        out.append(rec)
    return out
