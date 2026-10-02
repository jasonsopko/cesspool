"""Name the pool behind a block with reorg-watch's own rules, so both sites agree."""
import importlib.util, os, re

RW_PATH = os.environ.get("REORG_WATCH", os.path.expanduser("~/src/pr359-review/reorg-watch.py"))
CACHE = os.path.expanduser("~/.cesspool/pools-v2.json")

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


def slug(label):
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return s or "unknown"
