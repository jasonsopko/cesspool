"""Name the pool behind a block with reorg-watch's own rules, so both sites agree."""
import functools, importlib.util, json, os, re

RW_PATH = os.environ.get("REORG_WATCH", os.path.expanduser("~/src/pr359-review/reorg-watch.py"))
CACHE = os.path.join(os.environ.get("CESSPOOL_HOME", os.path.expanduser("~/.cesspool")), "pools-v2.json")
SURVEY = os.environ.get("POOL_SURVEY", os.path.expanduser("~/.reorg-watch/pool-survey.json"))

_rw = None
_pools = None


def _load():
    global _rw, _pools
    if _rw is None:
        spec = importlib.util.spec_from_file_location("reorg_watch", RW_PATH)
        _rw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_rw)
        _pools = _rw.Pools(CACHE)
    return _rw, _pools


def identify(coinbase_tx):
    """(pool label, coinbase tag) for a REST coinbase transaction."""
    rw, pools = _load()
    tag = rw.coinbase_tag(coinbase_tx["vin"][0].get("coinbase", ""))
    addrs = [v["scriptPubKey"]["address"] for v in coinbase_tx["vout"]
             if v["scriptPubKey"].get("address") and v.get("value", 0) > 0]
    return pools.identify(addrs, tag), tag


def builder(coinbase_tx, pool):
    """(class, miner tag, via) for whoever built the template, by reorg-watch's reading of the coinbase
    layout the DATUM gateway writes. D: a gateway with a DATUM pool upstream, so the gateway's node
    chose the transactions, normally the miner's own. G: a gateway running standalone, so the payout
    owner's node built it. O: other software, such as a pool's own stratum server. The miner tag is
    the gateway's second coinbase tag, empty when its owner set none. via is true for a D block charged
    to the DATUM miners of the pool it is named for: the name is a pool or marketplace in reorg-watch's
    pool survey, or, for a name the survey does not cover, the pool the block's first tag names. A D block
    whose name the survey calls an individual or a category, or that another pool's tag names, keeps
    its name: that name already points at the miner."""
    rw, pools = _load()
    sig = bytes.fromhex(coinbase_tx["vin"][0].get("coinbase", ""))
    vouts = [(round(v.get("value", 0) * 1e8), bytes.fromhex(v["scriptPubKey"]["hex"])) for v in coinbase_tx["vout"]]
    cls, primary, secondary = rw.classify_coinbase(sig, vouts)
    if cls != "D":
        return cls, "", False
    # classify_coinbase reads the tags as latin1; gateways write UTF-8
    primary, secondary = (x.encode("latin1").decode("utf-8", "replace") for x in (primary, secondary))
    kind = survey_kinds().get(pool)
    if kind in ("pool", "marketplace"):
        via = True
    elif kind in ("individual", "category"):
        via = False
    else:
        named = {n for t, n in pools.tags if t in primary and t.lower() != "datum"}
        via = pool in named or (len(pool) >= 3 and pool.lower() in primary.lower())
    return cls, secondary, via


@functools.lru_cache(maxsize=None)
def survey_kinds():
    """What reorg-watch's pool survey calls each name it covers: pool, individual, category or marketplace."""
    try:
        with open(SURVEY) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    rows = data.get("pools") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    return {r["label"]: r.get("kind") for r in rows if isinstance(r, dict) and isinstance(r.get("label"), str)}


DATUM_PREFIX = "DATUM miners via "


def who(pool, via):
    """The name a block's contents are charged to: the pool, or the DATUM miners building templates through it."""
    return DATUM_PREFIX + pool if via else pool


def slug(label):
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return s or "unknown"
