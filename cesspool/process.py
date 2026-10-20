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
    tmap, spam, txd, kp = [], [], [], {}
    for t in txs[1:]:
        fee = sats(t.get("fee", 0))
        total_fee += fee
        total_w += t["weight"]
        row = rows.get(t["txid"])
        k = classify.known(t)
        if k:
            kp[k] = kp.get(k, 0) + 1
        if row is None or "error" in row:
            tmap.append([t["vsize"], fee, 0, -1])
            txd.append(txdetail.tx_record(t, row, None, k))
            continue
        c = classify.classify(t, row)
        txd.append(txdetail.tx_record(t, row, c, k))
        tier = TIERS[c["tier"]]
        if tier:
            d = tx_detail(t, {"data_in": row.get("data_in", []), "data_out": row.get("data_out", [])}, c.get("fm_in"))
            spam.append({"txid": t["txid"], "tier": c["tier"], "types": c["types"], "labels": c["labels"],
                         "data": c["data_bytes"], "missed": c["missed"], "w": t["weight"], "vsize": t["vsize"], "size": t["size"],
                         "fee": fee, "v": {k: c["reasons"][k]["reasons"] for k in ("core", "knots", "plumb")},
                         "dc": {k: [c["reasons"][k]["data"], c["reasons"][k]["data_nonstd"]] for k in ("core", "knots", "plumb")},
                         "fc": row.get("fc", {}), "fix": row.get("fix", {}),
                         **({"p83": row["p83"]} if "p83" in row else {}),
                         **({"kp": k} if k else {}), **d})
            tmap.append([t["vsize"], fee, tier, len(spam) - 1])
        else:
            tmap.append([t["vsize"], fee, 0, -1])
    pool, tag = miner.identify(cb)
    bld, dtag, via, own = miner.builder(cb, pool)
    txd = {"h": b["height"], "hash": b["hash"], "t": b["time"], "pool": pool, "ps": miner.slug(pool),
           "bld": bld, "ws": miner.slug(miner.who(pool, via)), "cb": txdetail.coinbase_record(cb), "tx": txd}
    sewage = [s for s in spam if s["tier"] == "sewage"]
    gray = [s for s in spam if s["tier"] == "gray"]
    return txd, {
        "h": b["height"], "hash": b["hash"], "prev": b.get("previousblockhash"), "t": b["time"],
        "pool": pool, "tag": tag, "bld": bld, "dtag": dtag, "via": via, "own": own, "ntx": len(txs), "w": b["weight"],
        "size": b["size"], "reward": reward, "fees": total_fee, "txw": total_w,
        "sewage": {"n": len(sewage), "w": sum(s["w"] for s in sewage), "fee": sum(s["fee"] for s in sewage),
                   "data": sum(s["data"] for s in sewage), "missed": sum(1 for s in sewage if s["missed"])},
        "gray": {"n": len(gray), "w": sum(s["w"] for s in gray), "fee": sum(s["fee"] for s in gray),
                 "data": sum(s["data"] for s in gray)},
        "map": tmap, "spam": spam, "kp": kp,
    }


def reread_builder(h, rec):
    """Read who built block h again and rewrite its block and transaction records when that changed.
    True when it did."""
    cb = load_raw(h)["tx"][0]
    bld, dtag, via, own = miner.builder(cb, rec["pool"])
    if (rec.get("bld"), rec.get("dtag"), rec.get("via"), rec.get("own", "")) == (bld, dtag, via, own):
        return False
    rec["bld"], rec["dtag"], rec["via"], rec["own"] = bld, dtag, via, own
    tp = f"{TXD}/{h}.json"
    if os.path.exists(tp):
        with open(tp) as f:
            txd = json.load(f)
        txd["bld"], txd["ws"] = bld, miner.slug(miner.who(rec["pool"], via))
        write_json(tp, txd)
    write_json(f"{BLOCKS}/{h}.json", rec)
    return True


def refresh_own(idx, pairs=None):
    """Reread the DATUM-pool blocks still charged to DATUM miners via a pool, for the port identities and
    tag rule now in force. idx is the block index; pairs, when given, limits the work to blocks whose
    (pool, gateway tag) is among them. Returns the heights whose records changed."""
    changed = []
    for h in sorted(idx):
        s = idx[h]
        if s.get("bld") != "D" or not s.get("via"):
            continue
        if pairs is not None and (s.get("pool"), s.get("dtag") or "") not in pairs:
            continue
        with open(f"{BLOCKS}/{h}.json") as f:
            rec = json.load(f)
        if reread_builder(h, rec):
            changed.append(h)
    return changed


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
