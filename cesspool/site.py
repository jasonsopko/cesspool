"""Render cesspool.lol as static files from the block records."""
import collections, datetime, functools, gzip, hashlib, html, json, math, os, re, shutil, subprocess, tempfile, textwrap, time

from . import address, classify, found, miner
from .types import TYPES, info, KNOTS, PLUMB, NONE, ALLOWED, NOT_SPAM, KNOWN, DATACARRIER

HOME = os.environ.get("CESSPOOL_HOME", os.path.expanduser("~/.cesspool"))
BLOCKS = f"{HOME}/blocks"
TXD = f"{HOME}/txd"
INDEX = f"{HOME}/index.json"
OG_DRAWN = f"{HOME}/og.json"   # sha256 of the SVG behind each share image; delete it to draw every card again
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILTERS = os.environ.get("PLUMB_FILTERS", os.path.expanduser("~/src/plumb/plumb/filters.json"))
SITE = "https://cesspool.lol"
PLUMB_REPO = "https://github.com/plumb-node/plumb"
PLUMB_RELEASE = "https://github.com/plumb-node/plumb/releases/latest"
INSTALLER = "https://github.com/jasonsopko/knots-datum-node"
PLUMB_TAG = "v29.4.3.knots20260508.plumb9"
PLUMB_NAME = "Plumb 9"
PLUMB_VERSION = f"{PLUMB_NAME} ({PLUMB_TAG})"  # the release the verdicts come from, as a page names it
KNOTS_NAME = "Knots 29.4.3"
DATACARRIER_SIZE = {"knots": 83, "plumb": 42}  # each one's default -datacarriersize; Plumb went back to 42 in plumb7
MAXSCRIPTSIZE_DEFAULT = 1650  # the Knots and Plumb default -maxscriptsize
DUSTRELAYFEE_DEFAULT = 3000  # the default -dustrelayfee, in sat/kvB
MIN_USEFUL_SCRIPT_LIMIT = 520  # plumb-check offers no -maxscriptsize below this
BYTESPERSIGOPSTRICT_DEFAULT = 20  # the Knots and Plumb default -bytespersigopstrict
MAX_USEFUL_SIGOP_STRICT = 55  # plumb-check offers no -bytespersigopstrict above this
MAX_DUST_RATE = 100000  # plumb-check searches -dustrelayfee up to this, in sat/kvB
FORK = 961640

GRADES = [  # key, label, css
    ("pristine", "Pristine", "g-pristine"),
    ("tainted", "Tainted", "g-tainted"),
    ("foul", "Foul", "g-foul"),
    ("raw", "Raw sewage", "g-raw"),
]
GRADE = {k: (label, cls) for k, label, cls in GRADES}
WINDOWS = [("24h", "24 hours", 86400, 3), ("7d", "7 days", 7 * 86400, 10),
           ("30d", "30 days", 30 * 86400, 20), ("all", "Since the fork", None, 30)]

esc = html.escape


def prose(text):
    """Escape, then set `backticked` spans in code."""
    parts = esc(text).split("`")
    return "".join(f"<code>{x}</code>" if i % 2 else x for i, x in enumerate(parts))


def plural(k, word):
    return f"{n(k)} {word}{'' if k == 1 else 's'}"


# ---------------------------------------------------------------- numbers

def n(x):
    return f"{x:,}"


def btc(sats, places=4):
    return f"{sats / 1e8:,.{places}f}"


def pct(x, places=1):
    if x == 0:
        return "0%"
    if x < 0.001:
        return "<0.1%"
    shown = f"{x * 100:.{places}f}%"
    if x < 1 and shown.startswith("100"):
        # anything short of all of it never reads 100%
        return f"{math.floor(x * 100 * 10**places) / 10**places:.{places}f}%"
    return shown


def size(b):
    if b >= 1e6:
        return f"{b / 1e6:.2f} MB"
    if b >= 1e3:
        return f"{b / 1e3:.1f} kB"
    return f"{b} B"


def utc(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def tm(t, ago=True):
    return f'<time data-t="{t}"{" data-ago" if ago else ""}>{utc(t)}</time>'


# ---------------------------------------------------------------- grading

GRADE_VERSION = 2  # bump when grade() changes: load_index then regrades every stored summary


def grade(sn, share):
    """Pristine is a block with no sewage, what a Plumb node at its defaults builds. Small notes inside the
    data allowance are gray water: the block page counts them, the grade does not."""
    if sn == 0:
        return "pristine"
    if share < 0.01:
        return "tainted"
    if share < 0.10:
        return "foul"
    return "raw"


LETTER_STEPS = [(0.0005, "A"), (0.0025, "B"), (0.01, "C"), (0.04, "D")]  # share of block weight below which


def letter(share, blocks_with):
    """Pool grade over a window: share of block weight that is sewage."""
    for below, L in LETTER_STEPS:
        if share < below:
            return L
    return "F"


LETTER_TEXT = {"A": "Clean tap", "B": "Mostly clean", "C": "Needs work", "D": "Polluter", "F": "Open sewer"}


# ---------------------------------------------------------------- summaries

def rejected(reasons):
    return any(classify.DATA_REASONS.match(r) for r in reasons)


def summarize(rec):
    ty = {}
    top = None
    for s in rec["spam"]:
        t = s["types"][0] if s["types"] else "unknown"
        e = ty.setdefault(t, [0, 0, 0, 0, 0, 0, 0])  # n, data, core, knots, plumb, sewage n, missed n
        e[0] += 1
        e[1] += s["data"]
        e[2] += rejected(s["v"]["core"])
        e[3] += rejected(s["v"]["knots"])
        e[4] += rejected(s["v"]["plumb"])
        e[5] += s["tier"] == "sewage"
        e[6] += bool(s["missed"])
        if s["tier"] == "sewage" and (top is None or s["data"] > top[2]):
            top = [s["txid"], t, s["data"]]
    sw = rec["sewage"]["w"]
    share = sw / rec["w"] if rec["w"] else 0
    plumb_stops = sum(1 for s in rec["spam"] if s["tier"] == "sewage" and rejected(s["v"]["plumb"]))
    plumb_stops_w = sum(s["w"] for s in rec["spam"] if s["tier"] == "sewage" and rejected(s["v"]["plumb"]))
    knots_stops_w = sum(s["w"] for s in rec["spam"] if s["tier"] == "sewage" and rejected(s["v"]["knots"]))
    return {
        "h": rec["h"], "hash": rec["hash"], "t": rec["t"], "pool": rec["pool"], "ntx": rec["ntx"],
        "w": rec["w"], "fees": rec["fees"], "reward": rec["reward"],
        "sn": rec["sewage"]["n"], "sw": sw, "sf": rec["sewage"]["fee"], "sd": rec["sewage"]["data"],
        "sm": rec["sewage"]["missed"], "gn": rec["gray"]["n"], "gw": rec["gray"]["w"],
        "ps": plumb_stops, "psw": plumb_stops_w, "ksw": knots_stops_w,
        "share": share, "grade": grade(rec["sewage"]["n"], share),
        "ty": ty, "top": top, "bld": rec.get("bld", ""), "dtag": rec.get("dtag", ""), "via": rec.get("via", False),
        "own": rec.get("own", ""),
        "kp": rec.get("kp", {}), "kpf": kp_flagged(rec),
    }


def kp_flagged(rec):
    """{protocol: [gray n, sewage n]} for the block's listed transactions that classify.known recognized."""
    out = {}
    for s in rec["spam"]:
        if s.get("kp"):
            out.setdefault(s["kp"], [0, 0])[s["tier"] == "sewage"] += 1
    return out


def load_index():
    idx = {}
    if os.path.exists(INDEX):
        with open(INDEX) as f:
            idx = {int(k): v for k, v in json.load(f).items()}
    changed = []  # heights whose summary differs: their pages get rendered again
    rewrite = False
    for name in os.listdir(BLOCKS):
        if not name.endswith(".json"):
            continue
        h = int(name[:-5])
        p = f"{BLOCKS}/{name}"
        m = os.path.getmtime(p)
        old = idx.get(h)
        stale = old is None or old.get("_m") != m  # a new or rewritten record always counts as changed
        if stale or old.get("_g") != GRADE_VERSION:
            with open(p) as f:
                rec = json.load(f)
            s = summarize(rec)
            s["_m"] = m
            s["_g"] = GRADE_VERSION
            idx[h] = s
            rewrite = True
            # A regrade touches every stored summary; only the blocks it changes need their pages again.
            if stale or {k: v for k, v in old.items() if k[0] != "_"} != {k: v for k, v in s.items() if k[0] != "_"}:
                changed.append(h)
    for h in [h for h in idx if not os.path.exists(f"{BLOCKS}/{h}.json")]:
        del idx[h]
        changed.append(h)
    if changed or rewrite:
        tmp = INDEX + ".tmp"
        with open(tmp, "w") as f:
            json.dump(idx, f, separators=(",", ":"))
        os.replace(tmp, INDEX)
    return idx, changed


# ---------------------------------------------------------------- aggregates

def pool_stats(blocks):
    by = {}
    for b in blocks:
        k = who(b)
        p = by.setdefault(k, {"pool": k, "blocks": 0, "dirty": 0, "pristine": 0, "nodata": 0, "w": 0, "sw": 0,
                                       "sn": 0, "sf": 0, "sd": 0, "sm": 0, "fees": 0, "psw": 0, "worst": None,
                                       "ty": collections.Counter()})
        p["blocks"] += 1
        p["dirty"] += b["sn"] > 0
        p["pristine"] += b["grade"] == "pristine"
        p["nodata"] += b["sn"] == 0 and b["gn"] == 0  # not one byte of data, not even a small note
        for k in ("w", "sw", "sn", "sf", "sd", "sm", "fees", "psw"):
            p[k] += b[k]
        if b["sn"] and (p["worst"] is None or b["sw"] > p["worst"]["sw"]):
            p["worst"] = b
        for t, e in b["ty"].items():
            if e[5]:
                p["ty"][t] += e[5]
    for p in by.values():
        p["share"] = p["sw"] / p["w"] if p["w"] else 0
        p["letter"] = letter(p["share"], p["dirty"])
        p["fee_share"] = p["sf"] / p["fees"] if p["fees"] else 0
    return by


def window_blocks(idx, seconds):
    hs = sorted(idx)
    if not hs:
        return []
    tip_t = idx[hs[-1]]["t"]
    if seconds is None:
        return [idx[h] for h in hs]
    return [idx[h] for h in hs if idx[h]["t"] > tip_t - seconds]


def totals(blocks):
    t = {"blocks": len(blocks), "w": 0, "sw": 0, "sn": 0, "sf": 0, "sd": 0, "sm": 0, "fees": 0, "psw": 0, "ksw": 0,
         "clean": 0, "nodata": 0, "raw": 0, "ntx": 0}
    for b in blocks:
        for k in ("w", "sw", "sn", "sf", "sd", "sm", "fees", "psw", "ksw", "ntx"):
            t[k] += b[k]
        t["clean"] += b["sn"] == 0
        t["nodata"] += b["sn"] == 0 and b["gn"] == 0
        t["raw"] += b["grade"] == "raw"
    t["share"] = t["sw"] / t["w"] if t["w"] else 0
    return t


def past(b):
    """How many of the block's transactions Knots refuses at its defaults. Plumb refuses every one of them too.
    Kinds that pass the bar on /about/#bar (sweeps, PowPeg Flyover spends) are refused too but not counted."""
    return sum(e[3] for t, e in b["ty"].items() if info(t)["filter"] != NOT_SPAM)


def past_kinds(b):
    return ", ".join(esc(info(t)["name"]) for t, e in sorted(b["ty"].items(), key=lambda x: -x[1][3]) if e[3] and info(t)["filter"] != NOT_SPAM)


@functools.lru_cache(maxsize=None)
def pool_addresses():
    by = collections.defaultdict(set)
    try:
        with open(miner.CACHE) as f:
            for e in json.load(f):
                by[e["name"]] |= set(e.get("addresses") or [])
    except (OSError, ValueError):
        pass
    return by


@functools.lru_cache(maxsize=None)
def named_by_payout(h, name):
    """True when block h pays an address on file for the name it carries, False when it does not (so
    only its coinbase text ties it to that name), None for an unknown miner or a missing record."""
    if name.startswith("Unknown ("):
        return None
    try:
        with open(f"{TXD}/{h}.json") as f:
            cb = json.load(f).get("cb") or {}
    except (OSError, ValueError):
        return None
    pay = {o.get("ad") for o in cb.get("o", []) if o.get("ad") and o.get("a", 0) > 0}
    return bool(pay & pool_addresses().get(name, set()))


def named_cell(b):
    v = named_by_payout(b["h"], b["pool"])
    return "" if v is None else ("payout address" if v else "coinbase text only")


def who(b):
    """The name a block's contents are charged to: its pool, or the DATUM miners building templates through it."""
    return miner.who(b["pool"], b.get("via"))


def short_who(b):
    return f'DATUM miner via {b["pool"]}' if who(b) != b["pool"] else b["pool"]


def own_text(b):
    """For a DATUM-pool block the pool's own gateway built: how the site knows the gateway is the pool's."""
    return {"port": "built by its public stratum port", "tag": "built by its own gateway"}.get(b.get("own") or "", "")


def by_html(b):
    """'mined by X', or 'built by a DATUM miner via X' when a DATUM gateway with the pool upstream built the template."""
    if who(b) != b["pool"]:
        return f'built by <a href="/pool/{miner.slug(who(b))}/">a DATUM miner</a> via {pool_link(b["pool"])}'
    o = own_text(b)
    return f'mined by {pool_link(b["pool"])}' + (f", {o}" if o else "")


def by_text(b):
    if who(b) != b["pool"]:
        return f'built by a DATUM miner via {b["pool"]}'
    o = own_text(b)
    return f'mined by {b["pool"]}' + (f", {o}" if o else "")


def dtm_line(rec):
    """For a block a DATUM gateway built: who chose its contents, and where the site counts it."""
    if who(rec) == rec["pool"]:
        if not rec.get("own"):
            return ""
        if rec["own"] == "port":
            how = "its public stratum port handed out the very job this block was found on, so the gateway behind that port is the pool&#39;s own"
        else:
            how = f'the gateway tags its blocks "{esc(rec.get("dtag") or "")}", the pool&#39;s own name'
        return (f'<p class="small muted" style="margin-top:10px;max-width:70ch">A DATUM gateway with {pool_link(rec["pool"])} upstream built this template, and {how}. '
                f'The pool&#39;s node chose the transactions, so this block counts toward {esc(rec["pool"])} like the rest of its blocks.</p>')
    tag = rec.get("dtag") or ""
    tagged = f', which tags its blocks "{esc(tag)}"' if tag else ", which set no tag of its own"
    return (f'<p class="small muted" style="margin-top:10px;max-width:70ch">A DATUM gateway with {pool_link(rec["pool"])} upstream built this template{tagged}. '
            f'With DATUM the gateway&#39;s node chooses the transactions, normally the miner&#39;s own; the pool sets who the coinbase pays and its first tag, not what goes in the block. '
            f'This block counts toward <a href="/pool/{miner.slug(who(rec))}/">{esc(who(rec))}</a>, not {esc(rec["pool"])}.</p>')


DATUM_NOTE = ("A block whose template came from a DATUM gateway with the pool upstream is listed under DATUM miners via that pool, "
              "apart from the pool's own blocks, because the gateway's node chose what went in, normally the miner's own. "
              "A block whose name is not a pool's, such as a miner known by payout address or tag, keeps that name. "
              "A pool that serves a stratum port through its own gateway looks the same from the chain, so such a block counts toward the pool "
              "only when the site knows the gateway is the pool's: reorg.watch keeps a client on every public stratum port and saw the port hand "
              "out the job the block was found on, or the gateway's own tag is the pool's name.")


def known_stats(idx):
    """{protocol: {"n", "gray", "sewage"}} summed over every block, from classify.known."""
    st = {}
    for b in idx.values():
        for k, c in b.get("kp", {}).items():
            st.setdefault(k, {"n": 0, "gray": 0, "sewage": 0})["n"] += c
        for k, (g, sw) in b.get("kpf", {}).items():
            e = st.setdefault(k, {"n": 0, "gray": 0, "sewage": 0})
            e["gray"] += g
            e["sewage"] += sw
    return st


def type_stats(idx):
    st = {}
    for h in sorted(idx):
        for t, e in idx[h]["ty"].items():
            s = st.setdefault(t, {"n": 0, "data": 0, "core": 0, "knots": 0, "plumb": 0, "sewage": 0, "missed": 0,
                                  "first": h, "last": h, "blocks": 0})
            for i, k in enumerate(("n", "data", "core", "knots", "plumb", "sewage", "missed")):
                s[k] += e[i]
            s["last"] = h
            s["blocks"] += 1
    return st


# ---------------------------------------------------------------- pieces

LOGO = ('<svg viewBox="0 0 64 64" aria-hidden="true"><defs><clipPath id="mc"><circle cx="32" cy="32" r="21"/></clipPath></defs>'
        '<circle cx="32" cy="32" r="29" fill="none" stroke="currentColor" stroke-width="4"/>'
        '<circle cx="32" cy="32" r="21" fill="none" stroke="currentColor" stroke-width="2"/>'
        '<g clip-path="url(#mc)" stroke="currentColor" stroke-width="2.4" opacity=".85">'
        + "".join(f'<line x1="{x}" y1="8" x2="{x}" y2="56"/>' for x in range(14, 54, 7))
        + "".join(f'<line x1="8" y1="{y}" x2="56" y2="{y}"/>' for y in range(14, 54, 7))
        + '</g><path d="M24 50 q8 6 16 0" fill="none" stroke="var(--water)" stroke-width="3" stroke-linecap="round"/></svg>')

PLUMB_MARK = ('<svg viewBox="-72 -72 144 144" aria-hidden="true"><defs><mask id="pm"><rect x="-90" y="-100" width="180" height="200" fill="#fff"/>'
              '<circle cx="0" cy="0" r="6.5" fill="#000"/><circle cx="0" cy="-63" r="4.5" fill="#000"/></mask></defs>'
              '<g fill="#C8102E" mask="url(#pm)"><path d="M-8,-10 Q-10,-38 -22,-52 L22,-52 Q10,-38 8,-10 Z"/>'
              '<path d="M10,-8 Q38,-10 52,-22 L52,22 Q38,10 10,8 Z"/><path d="M-10,-8 Q-38,-10 -52,-22 L-52,22 Q-38,10 -10,8 Z"/>'
              '<circle cx="0" cy="0" r="15"/><path d="M-6,10 L6,10 L6,15 C6,20 18,20 18,28 L18,36 L0,72 L-18,36 L-18,28 C-18,20 -6,20 -6,15 Z"/>'
              '<rect x="-4" y="-57" width="8" height="7"/><circle cx="0" cy="-63" r="9"/></g></svg>')

LIQUID = {"pristine": ("#8fe6ff", "#c8f4ff"), "tainted": ("#6e9a9c", "#97b7b4"),
          "foul": ("#8a7a48", "#a8986a"), "raw": ("#6b4f1d", "#8c6b30")}
SLUDGE = "#5a3f12"


def cube(b, w=128):
    """An isometric sample jar: liquid colored by grade, sludge as deep as the sewage share."""
    g = b["grade"]
    liq, top = LIQUID[g]
    f = 0.70
    sl = 0 if not b["sn"] else min(f, max(0.07, math.sqrt(b["share"]) * 1.1))
    if g == "raw":
        sl = f

    def band(lo, hi, color, op):
        y0, y1 = 74 * lo, 74 * hi
        return (f'<polygon points="8,{104 - y0} 64,{128 - y0} 64,{128 - y1} 8,{104 - y1}" fill="{color}" opacity="{op}"/>'
                f'<polygon points="64,{128 - y0} 120,{104 - y0} 120,{104 - y1} 64,{128 - y1}" fill="{color}" opacity="{op * 0.8:.2f}"/>')

    y = 74 * f
    parts = [
        '<svg viewBox="0 0 128 132" role="img">',
        '<polygon points="8,30 64,54 64,128 8,104" fill="var(--panel2)" stroke="var(--line)" stroke-width="1"/>',
        '<polygon points="64,54 120,30 120,104 64,128" fill="var(--panel)" stroke="var(--line)" stroke-width="1"/>',
        band(0, f, liq, 0.9),
        band(0, sl, SLUDGE, 0.95) if sl else "",
        f'<polygon points="8,{104 - y} 64,{128 - y} 120,{104 - y} 64,{80 - y}" fill="{SLUDGE if g == "raw" else top}" opacity=".95"/>',
        '<polygon points="64,6 120,30 64,54 8,30" fill="none" stroke="var(--line)" stroke-width="1.2"/>',
        '<polyline points="8,30 8,104 64,128 120,104 120,30" fill="none" stroke="var(--faint)" stroke-width="1.4" opacity=".7"/>',
        '<line x1="64" y1="54" x2="64" y2="128" stroke="var(--faint)" stroke-width="1" opacity=".5"/>',
    ]
    if g == "pristine":
        parts.append('<path d="M20 62 l0 22 M28 66 l0 10" stroke="#fff" stroke-width="2.5" opacity=".55" stroke-linecap="round"/>')
    parts.append("</svg>")
    return "".join(parts)


def cube_link(b):
    label, cls = GRADE[b["grade"]]
    return (f'<a class="cube" href="/block/{b["h"]}/">{cube(b)}<div class="h">{b["h"]}</div>'
            f'<div class="p">{esc(short_who(b))}</div><div class="g {cls}">{label}'
            f'{" " + pct(b["share"]) if b["sn"] else ""}</div></a>')


def stamp(g, small=False):
    label, cls = GRADE[g]
    return f'<span class="stamp{" sm" if small else ""} {cls}">{label}</span>'


def pool_link(name):
    return f'<a href="/pool/{miner.slug(name)}/">{esc(name)}</a>'


def barcell(x, mx, color="var(--sewage)"):
    w = 0 if mx <= 0 else max(2, round(90 * x / mx)) if x else 0
    return f'<span class="barbg"><span class="bar" style="width:{w}px;background:{color}"></span></span>'


def plumb_cta(headline="Run Plumb and this stays out of your blocks.", body=None):
    body = body or ("Plumb is Bitcoin Knots with every reviewed spam filter on by default. It is a drop-in replacement: "
                    "same binaries, same config file, same data directory. Payments pass. Data storage does not.")
    return f'''<div class="plumbcta">{PLUMB_MARK}<div>
<h3>{esc(headline)}</h3><p>{esc(body)}</p>
<p><a class="btn" href="{PLUMB_RELEASE}">Get Plumb</a><a class="btn ghost" href="/plumb/">What it filters</a></p></div></div>'''


def page(path, title, body, *, nav="", desc="", og=None, tip=None):
    og = og or "/og/site.png"
    nav_items = [("blocks", "/blocks/", "Blocks"), ("found", "/found/", "Your Blocks"), ("shame", "/shame/", "Hall of Shame"), ("past", "/past-defaults/", "Past the Defaults"),
                 ("check", "/check/", "Check Node"), ("guide", "/guide/", "Field Guide"), ("plumb", "/plumb/", "Plumb"), ("about", "/about/", "About"),
                 ("address", "/address/", "Address Lookup")]  # last, so it does not push Plumb out of view
    navh = "".join(f'<a href="{u}"{" class=\"on\"" if k == nav else ""}>{t}</a>' for k, u, t in nav_items)
    full_title = f"{title} · cesspool.lol" if title else "cesspool.lol · Bitcoin water quality, block by block"
    foot_tip = f'Tip {tip["h"]}, {tm(tip["t"])}. ' if tip else ""
    # Summary pages reload themselves when a new block lands; block, transaction, Your Blocks, Address Lookup, about and privacy pages do not.
    live = tip and not path.startswith("/block/") and path not in ("/tx/", "/found/", "/address/", "/about/", "/privacy/", "/404")
    body_attr = f' data-tip="{tip["h"]}"' if live else ""
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(desc)}">
<meta property="og:title" content="{esc(title or "cesspool.lol")}"><meta property="og:description" content="{esc(desc)}">
<meta property="og:image" content="{SITE}{og}"><meta property="og:url" content="{SITE}{path}"><meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="{SITE}{og}">
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml"><link rel="alternate" type="application/atom+xml" title="Foul and raw-sewage blocks" href="/feed.xml"><link rel="stylesheet" href="/static/site.css?v={ASSET_V}">
</head><body{body_attr}>
<header class="top"><div class="wrap"><a class="brand" href="/">{LOGO}<span>cesspool<span class="tld">.lol</span></span></a>
<nav class="main">{navh}</nav><span class="spacer"></span>
<form class="jump" role="search"><input placeholder="Height, txid, address, name" aria-label="Go to a block height or a transaction id, look up an address, or find the blocks of a pool or coinbase tag"></form></div></header>
<main class="wrap">{body}</main>
<footer><div class="wrap"><div>Verdicts from the policy code in {PLUMB_VERSION}, run on every transaction.<br>
{foot_tip}Pool names follow <a href="https://reorg.watch">reorg.watch</a>.</div>
<div>We never display what spam carries.<br><a href="/about/">How this works</a> · <a href="/privacy/">Privacy</a> · <a href="https://github.com/jasonsopko/cesspool">Source</a></div>
<a class="discord" href="https://discord.gg/QxhQMdxrJ7"><img src="/static/beh-discord.png?v={ASSET_V}" width="28" height="28" alt=""><span>Bitcoin Education Hub on Discord: discuss Bitcoin openly, without being banned for your ideas.</span></a></div></footer>
<script src="/static/site.js?v={ASSET_V}" defer></script></body></html>
'''


ASSET_V = "36"


def write(out, rel, text):
    p = os.path.join(out, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, p)


# ---------------------------------------------------------------- block page

def group_rows(rows, kind):
    """Collapse consecutive identical inputs or outputs into one line."""
    out = []
    for r in rows:
        key = (r["type"], r["data"], r["sats"] if kind == "out" else None)
        if out and out[-1][0] == key:
            out[-1][1] += 1
        else:
            out.append([key, 1, r])
    return out


TYPE_SHORT = {"witness_v0_keyhash": "P2WPKH", "witness_v0_scripthash": "P2WSH", "witness_v1_taproot": "P2TR",
              "scripthash": "P2SH", "pubkeyhash": "P2PKH", "nulldata": "OP_RETURN", "multisig": "Bare multisig",
              "pubkey": "P2PK", "anchor": "Anchor", "witness_unknown": "Unknown witness", "nonstandard": "Nonstandard"}


def io_html(rows, kind, limit=8, missed=False):
    g = group_rows(rows, kind)
    parts = []
    for (key, cnt, r) in g[:limit]:
        t = TYPE_SHORT.get(r["type"], r["type"] or "?")
        extra = f'{n(r["sats"])} sat' if kind == "out" else (f'{n(r["wbytes"])} B witness' if r["wbytes"] else "")
        d = f'<span class="db">{n(r["data"] * cnt)} B data{" (" + PLUMB_NAME + " misses)" if missed and kind == "in" else ""}</span>' if r["data"] else '<span class="x">payment</span>'
        if kind == "out" and r["type"] == "nulldata":
            d = f'<span class="db">{n(r["data"] * cnt)} B data</span>' if r["data"] else f'<span class="db">{r["len"]} B note</span>'
        parts.append(f'<div class="row{" d" if r["data"] or (kind == "out" and r["type"] == "nulldata") else ""}">'
                     f'<span>{"&times;" + n(cnt) if cnt > 1 else ""}</span><span>{t} <span class="x">{extra}</span></span>{d}</div>')
    if len(g) > limit:
        rest = sum(c for _, c, _ in g[limit:])
        parts.append(f'<div class="row"><span></span><span class="x">{n(rest)} more</span><span></span></div>')
    return "".join(parts)


# Bitcoin Core is graded too (plumb_check's core profile) but not shown: it does not accept block 961640
# or any block after it, so no Core node has these blocks.
VERDICT_NAMES = [("knots", KNOTS_NAME), ("plumb", PLUMB_NAME)]


def source_link(opt):
    s = FILTER_SOURCE.get(opt) if opt in PLUMB_OWN else KNOTS_SOURCE.get(opt) or FILTER_SOURCE.get(opt)
    return f' (<a href="{esc(s[1])}">{esc(s[0])}</a>)' if s else ""


def rule_list(rs, dc, k):
    """One line per reason: the code, what it means, the option behind it. `dc` is the policy's
    [OP_RETURN bytes, other bytes] count, shown on the data-carrier reasons; `k` the profile, whose
    -datacarriersize default the size reason names."""
    items = []
    for r in rs:
        words, opt = RULES.get(r, ("", None))
        if r == "txn-datacarrier-exceeded":
            words, opt = f"data counted, over the {DATACARRIER_SIZE[k]}-byte allowance", f"-datacarriersize={DATACARRIER_SIZE[k]}"
        if dc and r == "txn-datacarrier-nonstandard":
            words = f"{n(dc[1])} B of {words}"
        elif dc and r == "txn-datacarrier-exceeded":
            words = f"{n(dc[0] + dc[1])} B of {words}"
        items.append(f'<li><code>{esc(r)}</code>' + (f" {prose(words)}" if words else "")
                     + (f' <span class="opt">{esc(opt)}</span>' if opt else "") + "</li>")
    return f'<ul class="rules">{"".join(items)}</ul>'


def plumb_own(s, data_rs):
    """Plumb's own filters behind a refusal, most bytes first: those measured in fc, plus any a reason names,
    plus knots#470's recount when it counts bytes."""
    fc = s.get("fc") or {}
    own = (set(fc) & PLUMB_OWN) | {RULES[r][1] for r in data_rs if r in RULES and RULES[r][1] in PLUMB_OWN}
    if fc.get(RECOUNT):
        own.add(RECOUNT)
    return sorted(own, key=lambda o: -fc.get(o, 0))


def own_name(o):
    """How a refusal sentence names one of Plumb's own filters."""
    if o == RECOUNT:
        return f'recount from verified keys (<a href="{esc(RECOUNT_URL)}">{esc(RECOUNT)}</a>, under <code>-rejectfakemultisig</code>)'
    return f"<code>{esc(o)}</code>"


def knots_bytes(fc, o):
    """The bytes Knots' rule o counts, as Plumb measured it: Plumb's -rejectfakemultisig count holds its recount
    from verified keys too, which Knots does not count."""
    return fc.get(o, 0) - (fc.get(RECOUNT, 0) if o == "-rejectfakemultisig" else 0)


def knots_measured(s):
    """Knots' own data-counting rules behind a refusal, most bytes first, as the engine measured them."""
    fc = s.get("fc") or {}
    return sorted((o for o in KNOTS_RULES if knots_bytes(fc, o)), key=lambda o: -knots_bytes(fc, o))


def feerate_conf(sat_per_kvb):
    """A sat/kvB rate the way bitcoin.conf takes it, in BTC/kvB."""
    return f"0.{sat_per_kvb:08d}"


def refuse_with(k, name, s, counted):
    """For a transaction `name` relays: the settings whose value would refuse it. plumb-check found each
    value by rerunning the policy checks with that one setting changed ("fix"); nothing here is reasoned
    from the transaction. A setting no value of refuses it is left out; -maxscriptsize is offered only
    at MIN_USEFUL_SCRIPT_LIMIT or more, -bytespersigopstrict only up to MAX_USEFUL_SIGOP_STRICT."""
    fix = (s.get("fix") or {}).get(k)
    if fix is None:
        return ""
    parts = []
    if "dcs" in fix:
        setting = "<code>datacarrier=0</code>" if fix["dcs"] == 0 else f'<code>datacarriersize={fix["dcs"]}</code> or lower'
        parts.append(f"{setting} (counts {n(counted)} B; default {DATACARRIER_SIZE[k]})")
    if "mss" in fix:
        parts.append(f'<code>maxscriptsize={fix["mss"]}</code> or lower (default {n(MAXSCRIPTSIZE_DEFAULT)}; it also refuses '
                     f'every ordinary multisig spend bigger than that, <a href="/check/#fake-multisig">see why</a>)')
    if "bpss" in fix:
        higher = " or higher" if fix["bpss"] < MAX_USEFUL_SIGOP_STRICT else ""
        parts.append(f'<code>bytespersigopstrict={fix["bpss"]}</code>{higher} (default {BYTESPERSIGOPSTRICT_DEFAULT}; '
                     f'it can refuse some wide multisig too)')
    if "dust" in fix:
        # The dust line depends on the output type, so name the output the engine saw fall under it
        outs = s.get("outs") or []
        o = outs[fix["dusti"]] if fix.get("dusti") is not None and fix["dusti"] < len(outs) else None
        kind = TYPE_SHORT.get(o["type"], o["type"]) if o else None
        which = f"a {kind} output of {n(o['sats'])} sat" if o else "one of its outputs"
        if fix["dust"] == DUSTRELAYFEE_DEFAULT + 1:
            parts.append(f"any <code>dustrelayfee</code> above the default {feerate_conf(DUSTRELAYFEE_DEFAULT)} ({which} sits on the dust line)")
        else:
            also = f", and every {kind} output that small with it" if kind else ""
            parts.append(f'<code>dustrelayfee={feerate_conf(fix["dust"])}</code> or higher (default {feerate_conf(DUSTRELAYFEE_DEFAULT)}), '
                         f'which makes {which} dust{also}')
    if parts:
        return f'<div class="by">Would refuse it with: {"; ".join(parts)}.</div>'
    # Name Plumb's filter only when Plumb refuses the transaction; fc alone measures bytes
    own = plumb_own(s, [r for r in s["v"]["plumb"] if classify.DATA_REASONS.match(r)]) if k == "knots" and s["v"]["plumb"] else []
    does = f" {esc(PLUMB_NAME)}&#39;s {', '.join(own_name(o) for o in own)} {'do' if len(own) > 1 else 'does'}." if own else ""
    return (f'<div class="by">No <code>datacarriersize</code>, no <code>maxscriptsize</code> of {MIN_USEFUL_SCRIPT_LIMIT} or more, no '
            f'<code>bytespersigopstrict</code> up to {MAX_USEFUL_SIGOP_STRICT} and no '
            f'<code>dustrelayfee</code> up to {feerate_conf(MAX_DUST_RATE)} refuses it.{does}</div>')


def plumb_by(s, data_rs):
    """What a Plumb refusal rests on: its own filters, each measured with that filter alone off, its 42-byte
    -datacarriersize default ("p83": Plumb's reasons at Knots' 83, there when 42 changes them), or Knots' rules."""
    knots_rs = set(s["v"]["knots"])
    fc = s.get("fc") or {}
    dc = s.get("dc") or {}
    by42 = "p83" in s
    size42 = f'{PLUMB_NAME}&#39;s default <code>datacarriersize={DATACARRIER_SIZE["plumb"]}</code>'
    if by42:
        data_rs = [r for r in data_rs if r != "txn-datacarrier-exceeded"]
        if not data_rs:
            relays = ", so stock Knots relays it" if not s["v"]["knots"] else ""
            return (f'<div class="by">Refused by {size42}: it counts {n(sum(dc.get("plumb", [0, 0])))} B. '
                    f'Knots&#39; default is {DATACARRIER_SIZE["knots"]}{relays}.</div>')
    added = sum(dc.get("plumb", [0, 0])) - sum(dc.get("knots", [0, 0]))
    own = plumb_own(s, data_rs)
    parts = [own_name(o) + (f" counts {n(fc[o])} B" if fc.get(o) else "") + (source_link(o) if o != RECOUNT else "") for o in own]
    if added > 0 and not (set(fc) & (PLUMB_OWN | {RECOUNT})):
        # Two filters covering the same bytes: neither changes the count alone, together they do.
        parts.append(f"its filters together count {n(added)} B that Knots does not")
    knots_refuses = any(classify.DATA_REASONS.match(r) for r in knots_rs)
    # Knots' own rules, with the bytes each one counts, where the engine measured them
    kparts = [f"<code>{esc(o)}</code> counts {n(knots_bytes(fc, o))} B{source_link(o)}" for o in knots_measured(s)]
    krules = f'Knots&#39; rules refuse it: {"; ".join(kparts)}.' if kparts else "Knots&#39; rules refuse it."
    also = f" It is also over {size42}; Knots&#39; is {DATACARRIER_SIZE['knots']}." if by42 else ""
    if parts:
        if knots_refuses:
            return f'<div class="by">{krules} Plumb&#39;s own filters add: {"; ".join(parts)}.{also}</div>'
        return f'<div class="by">Refused by Plumb&#39;s own filters: {"; ".join(parts)}.{also}</div>'
    if knots_refuses:
        return f'<div class="by">{krules} None of Plumb&#39;s own filters is needed here.{also}</div>'
    extra = [r for r in data_rs if r not in knots_rs]
    return f'<div class="by">Knots relays it; the refusal comes from rules Knots does not have: {", ".join(f"<code>{esc(r)}</code>" for r in extra)}.{also}</div>'


def verdict_html(s):
    cells = []
    for k, name in VERDICT_NAMES:
        rs = s["v"][k]
        dc = (s.get("dc") or {}).get(k)
        total = sum(dc) if dc else 0
        data_rs = [r for r in rs if classify.DATA_REASONS.match(r)]
        if rs:
            res = '<span class="stop">Refuses it</span>'
            why = rule_list(rs, dc, k)
            if not data_rs:
                why += '<div class="by">Not a data rule.</div>'
            elif k == "plumb":
                why += plumb_by(s, data_rs)
        else:
            res = '<span class="pass">Relays and mines it</span>'
            text = "this one gets past its filters" if k == "plumb" and s["missed"] else "no rule matches"
            text += f"; counts {n(total)} B of data, inside the {DATACARRIER_SIZE[k]}-byte allowance" if total else ", no data counted"
            why = f'<div class="why">{text}</div>' + refuse_with(k, name, s, total)
        cells.append(f'<div class="{"plumb" if k == "plumb" else ""}"><div class="who">{name}</div>{res}{why}</div>')
    return f'<div class="verd">{"".join(cells)}</div>'


def filter_line(t):
    i = info(t)
    if i.get("option") == DATACARRIER:
        return (f'{PLUMB_NAME}&#39;s default <code>datacarriersize={DATACARRIER_SIZE["plumb"]}</code> refuses this kind once it counts '
                f'more than {DATACARRIER_SIZE["plumb"]} bytes; Knots&#39; default is {DATACARRIER_SIZE["knots"]}.')
    if i.get("option") and i["filter"] in (PLUMB, KNOTS):
        prs = " ".join(f'<a href="{u}">{esc(l)}</a>' for l, u in i.get("prs", []))
        if i.get("knots_since") and i["option"] in KNOTS_HAS_NO_SWITCH:
            # Knots took the rule in without a switch; the option is Plumb's
            return (f'Knots counts it as data by default since {i["knots_since"]}{" (" + prs + ")" if prs else ""}; '
                    f'Plumb keeps a switch, <code>{esc(i["option"])}</code>.')
        if i.get("knots_since"):
            return f'Knots ships <code>{esc(i["option"])}</code> since {i["knots_since"]}{" (" + prs + ")" if prs else ""}.'
        who = "Plumb ships" if i["filter"] == PLUMB else "Knots ships"
        return f'{who} <code>{esc(i["option"])}</code>{" (" + prs + ")" if prs else ""}.'
    if i["filter"] == NOT_SPAM:
        return f'Refused under <code>{esc(i["option"])}</code>, but <a href="/about/#bar">not spam</a>, so not counted as sewage.'
    if i["filter"] == KNOTS:
        return "Knots counts it as data by default."
    if i["filter"] == NONE and i.get("prs"):
        prs = ", ".join(f'<a href="{u}">{esc(l)}</a>' for l, u in i["prs"])
        return f"No shipped filter yet. In review: {prs}."
    return ""


def dissection(s, paid_to, rate_ctx):
    t = s["types"][0] if s["types"] else "unknown"
    i = info(t)
    cls = "dis" + (" gray" if s["tier"] == "gray" else "") + (" missed" if s["missed"] else "")
    tier_stamp = (f'<span class="stamp sm g-raw">Sewage</span>' if s["tier"] == "sewage" else '<span class="stamp sm" style="color:var(--gray)">Gray water</span>')
    if s["missed"]:
        tier_stamp += ' <span class="stamp sm" style="color:var(--miss)">Plumb miss</span>'
    vbytes = s["vsize"]
    # A run type's payload is counted in the reveal; show the bytes the policy code counted here.
    shown = sum(r["data"] for r in s["ins"]) + sum(r["data"] for r in s["outs"]) if i.get("staged") else s["data"]
    share = min(1, shown / max(1, s.get("size", s["w"] / 4))) if shown else 0
    feerate = s["fee"] / vbytes if vbytes else 0
    others = "".join(f'<span class="chip">{esc(l)}</span>' for l in s["labels"][1:])
    note = ""
    if s["missed"]:
        note = f'<div class="note"><b>{PLUMB_NAME} relays this.</b> {prose(i.get("note", ""))} {filter_line(t)}</div>'
    return f'''<section class="{cls}" id="tx-{s["txid"][:16]}">
<div class="hd">{tier_stamp}<h3>{esc(i["name"])}</h3><span class="muted small">{esc(s["labels"][0]) if s["labels"] else ""}</span></div>
<div class="txid"><a href="/tx/{s["txid"]}">{s["txid"]}</a></div>
<p>{prose(i["what"])} <span class="muted">{prose(i["how"])}</span></p>
{f'<p class="small">{others}</p>' if others else ""}
<div class="anat"><div><h4>Inputs ({n(len(s["ins"]))})</h4><div class="io">{io_html(s["ins"], "in", missed=s["missed"])}</div></div>
<div><h4>Outputs ({n(len(s["outs"]))})</h4><div class="io">{io_html(s["outs"], "out")}</div></div></div>
<div class="small muted">{"Counted as data" if i.get("staged") else "Payload"}: <b class="num" style="color:var(--text)">{n(shown)} bytes</b> of a {n(s.get("size", vbytes))}-byte transaction ({n(vbytes)} vB).
Fee <span class="num">{n(s["fee"])}</span> sat ({feerate:.1f} sat/vB), paid {paid_to}.</div>
<div class="fill" title="Share of the transaction that is {"counted as data" if i.get("staged") else "payload"}"><span style="width:{share * 100:.1f}%;background:var(--sewage)"></span><span style="flex:1"></span></div>
{verdict_html(s)}
{f'<p class="small" style="margin:10px 0 0">{filter_line(t)}</p>' if filter_line(t) and not s["missed"] and not (i.get("option") == DATACARRIER and "p83" in s) else ""}
{note}
</section>'''


PAYOUTS_SHOWN = 10  # coinbase outputs listed before the rest go under "The other N outputs"


def coinbase_payouts(rec):
    """Every output the coinbase pays, largest first, each address opening Address Lookup."""
    try:
        with open(f"{TXD}/{rec['h']}.json") as f:
            cb = json.load(f).get("cb") or {}
    except (OSError, ValueError):
        return ""
    outs = sorted((o for o in cb.get("o", []) if o.get("a", 0) > 0), key=lambda o: -o["a"])
    if not outs:
        return ""
    total = sum(o["a"] for o in outs)
    pool = rec["pool"]
    on_file = pool_addresses().get(pool, set())

    def row(o):
        ad = o.get("ad")
        to = (f'<code class="ad">{esc(ad)}</code> <a class="small" href="/address/#{esc(ad)}">look up</a>' if ad
              else f'<span class="muted">{esc(TYPE_SHORT.get(o["t"], o["t"]))} output, no address</span>')
        note = f"on file for {esc(pool)}" if ad in on_file else ""
        return f'<tr><td>{to}</td><td class="r num">{btc(o["a"], 8)}</td><td class="r num">{pct(o["a"] / total)}</td><td class="small muted">{note}</td></tr>'

    head = '<tr><th>Paid to</th><th class="r">BTC</th><th class="r">Share</th><th></th></tr>'
    shown, rest = outs[:PAYOUTS_SHOWN], outs[PAYOUTS_SHOWN:]
    other = "The other output" if len(rest) == 1 else f"The other {n(len(rest))} outputs"
    more = (f'<details class="more"><summary>{other}</summary>'
            f'<div class="tw"><table>{head}{"".join(row(o) for o in rest)}</table></div></details>') if rest else ""
    n_ad = sum(1 for o in outs if o.get("ad"))
    look = ("" if not n_ad else " The address has a look up link to Address Lookup." if n_ad == 1
            else " Each address has a look up link to Address Lookup.")
    return (f'<h2>Coinbase payouts</h2><p class="muted">The <a href="/tx/{esc(cb["id"])}">coinbase</a> pays {btc(total, 8)} BTC in '
            f'{plural(len(outs), "output")}.{look}</p>'
            f'<div class="tw"><table>{head}{"".join(row(o) for o in shown)}</table></div>{more}')


def gray_table(gray):
    if not gray:
        return ""
    rows = []
    for s in gray[:400]:
        t = s["types"][0] if s["types"] else "unknown"
        rows.append(f'<tr id="tx-{s["txid"][:16]}"><td><a href="/guide/#{t}">{esc(info(t)["name"])}</a></td><td class="mono small"><a href="/tx/{s["txid"]}">{s["txid"][:16]}…</a></td>'
                    f'<td class="r num">{n(s["data"])}</td><td class="r num">{n(s["vsize"])}</td><td class="r num">{n(s["fee"])}</td></tr>')
    more = f'<p class="small muted">{n(len(gray) - 400)} more not listed.</p>' if len(gray) > 400 else ""
    ns = sum(1 for s in gray if s["types"] and info(s["types"][0])["filter"] == NOT_SPAM)
    what = f"small notes inside {PLUMB_NAME}&#39;s {DATACARRIER_SIZE['plumb']}-byte default" + (", and refused transactions that are not spam" if ns else "")
    return f'''<details class="more"><summary>{n(len(gray))} gray-water transactions: {what}</summary>
<div class="tw"><table><tr><th>Kind</th><th>Transaction</th><th class="r">Data bytes</th><th class="r">vB</th><th class="r">Fee sat</th></tr>{"".join(rows)}</table></div>{more}</details>'''


def block_page(rec, s, prev_h, next_h, tip):
    label, cls = GRADE[s["grade"]]
    sew = [x for x in rec["spam"] if x["tier"] == "sewage"]
    gray = [x for x in rec["spam"] if x["tier"] == "gray"]
    sew.sort(key=lambda x: (-x["missed"], -x["data"]))
    mapdata = {"h": rec["h"], "pn": PLUMB_NAME, "map": rec["map"], "spam": [{"id": x["txid"][:16], "n": info(x["types"][0])["name"] if x["types"] else "Data",
                                             "d": x["data"], "m": 1 if x["missed"] else 0} for x in rec["spam"]]}
    pool = rec["pool"]
    dtm = who(rec) != pool
    total_fees = rec["fees"]
    if s["sn"]:
        paid = "paid" if dtm else f"paid {esc(pool)}"
        verdict = (f'{n(s["sn"])} sewage transaction{"s" if s["sn"] != 1 else ""} took <b>{pct(s["share"])}</b> of this block '
                   f'and {paid} <b>{btc(s["sf"])} BTC</b>, {pct(s["sf"] / total_fees if total_fees else 0)} of its fees.')
        if s["sm"]:
            verdict += (" One of them gets" if s["sm"] == 1 else f' {n(s["sm"])} of them get') + f" past {PLUMB_NAME}'s filters."
        caught = s["ps"]
        if caught:
            verdict += f' A Plumb node refuses {n(caught)} of the {n(s["sn"])}.'
    elif s["gn"]:
        verdict = f'No sewage. {n(s["gn"])} small note{"s" if s["gn"] != 1 else ""} inside {PLUMB_NAME}&#39;s default {DATACARRIER_SIZE["plumb"]}-byte data allowance.'
    else:
        verdict = "Every transaction in this block is a payment. Not one byte of data."
    k = past(s)
    past_line = ""
    if k:
        what = "One of its transactions is one" if k == 1 else f"{n(k)} of its transactions are ones"
        them = "it" if k == 1 else "them"
        text_only = named_by_payout(rec["h"], pool) is False
        past_line = (f'<p style="margin-top:10px;max-width:70ch"><b style="color:var(--miss)">Past the defaults.</b> '
                     f'{what} Knots and Plumb refuse at their default settings. A node at those defaults does not accept {them} '
                     f'from peers or put {them} in a block unless its operator overrides the refusal.'
                     + (f' The name {esc(pool)} comes from this block&#39;s coinbase text alone, which anyone can write.' if text_only else "")
                     + f' <a href="/past-defaults/">Every block like this</a>. {SETTINGS_LINK}.</p>')
    nav = (f'<div class="navpn">{f"<a href=/block/{prev_h}/>&larr; {prev_h}</a>" if prev_h else ""}'
           f'{f"<a href=/block/{next_h}/>{next_h} &rarr;</a>" if next_h else ""}</div>')
    share_text = f"Block {rec['h']}, {by_text(rec)}: {label.lower()}" + (f", {pct(s['share'])} of the block is spam." if s["sn"] else ".")
    body = f'''<div class="bhead"><div class="t"><div class="kicker">Block {n(rec["h"])} · sample report</div>
<h1>Block {rec["h"]}</h1>
<div class="meta"><span>{by_html(rec)[0].upper() + by_html(rec)[1:]}</span><span>{tm(rec["t"])}</span><span><b class="num">{n(rec["ntx"])}</b> transactions</span>
<span><b class="num">{rec["w"] / 4e6 * 100:.0f}%</b> full</span><span>Fees <b class="num">{btc(total_fees)}</b> BTC</span></div>
<p style="margin-top:14px;max-width:70ch">{verdict}</p>{past_line}{dtm_line(rec)}
<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">{nav}<button class="copy" data-copy="{esc(share_text)} {SITE}/block/{rec["h"]}/">Copy share link</button></div></div>
<div class="stampbox">{stamp(s["grade"])}</div></div>
<div class="mapbox"><canvas id="map" aria-label="Map of every transaction in the block, sized by virtual size"></canvas><div class="tip"></div></div>
<div class="legend"><span><i style="background:var(--water);opacity:.55"></i>Payment</span><span><i style="background:var(--gray)"></i>Gray water (small note)</span>
<span><i style="background:var(--sewage)"></i>Sewage</span><span><i style="border:2px solid var(--miss)"></i>Sewage {PLUMB_NAME} misses</span></div>
<script type="application/json" id="mapdata">{json.dumps(mapdata, separators=(",", ":"))}</script>
'''
    body += coinbase_payouts(rec)
    if sew:
        body += f'<h2>Dissection</h2><p class="muted">Every sewage transaction in the block, what it hides and where, and what each node policy does with it.</p>'
        body += "".join(dissection(x, "into this block&#39;s coinbase" if dtm else f"to {esc(pool)}", None) for x in sew[:200])
        if len(sew) > 200:
            body += f'<p class="muted">{n(len(sew) - 200)} more sewage transactions not shown.</p>'
        if s["ps"]:
            body += plumb_cta(f"A Plumb node refuses {n(s['ps'])} of these {n(s['sn'])}.")
    body += gray_table(gray)
    og = f"/og/block/{rec['h']}.png" if s["sn"] else None
    desc = f"{by_text(rec)[0].upper() + by_text(rec)[1:]}. " + (f"{pct(s['share'])} of the block is spam: {n(s['sn'])} transactions." if s["sn"] else f"{label}: no sewage.")
    return page(f"/block/{rec['h']}/", f"Block {rec['h']}: {label}", body, nav="blocks", desc=desc, og=og, tip=tip)


# ---------------------------------------------------------------- transaction page

# The rule behind each refusal reason: what it means and the option it comes from, at the defaults.
# A reason not listed is shown by its code alone. The options are stock Knots' unless PLUMB_OPTIONS has them.
RULES = {
    "tokens-olga": ("the OLGA `stamp:` framing in P2WSH outputs", "-rejecttokens"),
    "tokens-runes": ("a Runes message in OP_RETURN", "-rejecttokens"),
    "tokens-counterparty": ("a Counterparty message in OP_RETURN", "-rejecttokens"),
    "tokens-json": ("a JSON token message in OP_RETURN", "-rejecttokenmessages"),
    "tokens-omni": ("an Omni Layer message in OP_RETURN", "-rejecttokenmessages"),
    "parasite-cat21": ("a CAT-21 mint, nLockTime 21", "-rejectparasites"),
    "txn-datacarrier-nonstandard": ("data outside OP_RETURN", "-acceptnonstddatacarrier=0"),
    "txn-datacarrier-exceeded": ("data counted, over the allowance", "-datacarriersize"),
    "bare-datacarrier": ("an OP_RETURN with no payment output beside it", "-permitbaredatacarrier=0"),
    "bare-multisig": ("a bare multisig output", "-permitbaremultisig=0"),
    "bare-pubkey": ("a bare public key output", "-permitbarepubkey=0"),
    "multi-op-return": ("more than one OP_RETURN output", None),
    "dust": ("an output below the dust limit", "-dustrelayfee"),
    "scriptpubkey": ("an output script the policy does not relay, such as an OP_RETURN over the data size limit", None),
    "version": ("a transaction version outside 1 to 3", None),
    "tx-size": ("a transaction over the size limit", None),
    "bad-txns-too-many-sigops": ("over 16,000 signature checks, or too few weight units for each", "-bytespersigopstrict"),
}


def tx_page(tip):
    body = f'''<div id="txapp" data-plumb="{esc(PLUMB_NAME)}">
<div class="hero"><div class="kicker">Transaction</div><h1>Look up a transaction</h1>
<p class="lede">Paste a transaction id from any block, height {FORK} on. The page shows what the transaction does, what it carries, and what Knots and {esc(PLUMB_NAME)} would do with it.</p></div>
<form class="txform"><input class="mono" spellcheck="false" autocomplete="off" placeholder="Transaction id (64 hex characters)" aria-label="Transaction id"><button class="btn">Look up</button></form>
<div class="txout" aria-live="polite"></div>
<noscript><p class="muted">This page needs JavaScript to load the transaction.</p></noscript></div>'''
    return page("/tx/", "Transaction", body, desc="What a Bitcoin transaction does, what it carries, and what each node policy does with it.", tip=tip)


def write_gz(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wb", compresslevel=6) as f:
        f.write(json.dumps(obj, separators=(",", ":")).encode())
    os.replace(tmp, path)


def read_gz(path):
    try:
        with gzip.open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def shard_key(txid):
    """Index shard (first three hex characters) and the key within it (the next 13)."""
    return txid[:3], txid[3:16]


def txids_of(h):
    with open(f"{TXD}/{h}.json") as f:
        d = json.load(f)
    return [d["cb"]["id"]] + [t["id"] for t in d["tx"]]


def publish_txd(out, heights):
    """Copy the transaction files for `heights` into the site and point the index at them."""
    shards = collections.defaultdict(dict)
    heights = [h for h in heights if os.path.exists(f"{TXD}/{h}.json")]
    for h in heights:
        for txid in txids_of(h):
            s, k = shard_key(txid)
            shards[s][k] = h
    # Index first: a block file that is missing gets published again, an index entry would not.
    for s, entries in shards.items():
        p = f"{out}/d/i/{s}.json.gz"
        cur = read_gz(p)
        cur.update(entries)
        write_gz(p, cur)
    for h in heights:
        with open(f"{TXD}/{h}.json") as f:
            write_gz(f"{out}/d/b/{h}.json.gz", json.load(f))


def rebuild_tx_index(out, heights):
    """Write every index shard from scratch, through 16 temporary files to keep memory small."""
    with tempfile.TemporaryDirectory(dir=HOME) as tmp:
        files = {c: open(f"{tmp}/{c}", "w") for c in "0123456789abcdef"}
        for h in heights:
            if os.path.exists(f"{TXD}/{h}.json"):
                for txid in txids_of(h):
                    files[txid[0]].write(f"{txid} {h}\n")
        for f in files.values():
            f.close()
        for c in "0123456789abcdef":
            shards = collections.defaultdict(dict)
            with open(f"{tmp}/{c}") as f:
                for line in f:
                    txid, h = line.split()
                    s, k = shard_key(txid)
                    shards[s][k] = int(h)
            for s, entries in shards.items():
                write_gz(f"{out}/d/i/{s}.json.gz", entries)


def drop_from_tx_index(out, heights):
    """Before a reorg removes `heights`, take their transactions out of the index and the site."""
    shards = collections.defaultdict(set)
    for h in heights:
        if not os.path.exists(f"{TXD}/{h}.json"):
            continue
        for txid in txids_of(h):
            s, k = shard_key(txid)
            shards[s].add(k)
        p = f"{out}/d/b/{h}.json.gz"
        if os.path.exists(p):
            os.remove(p)
    for s, keys in shards.items():
        p = f"{out}/d/i/{s}.json.gz"
        cur = read_gz(p)
        for k in keys:
            if cur.get(k) in heights:
                del cur[k]
        write_gz(p, cur)


def tx_catalog():
    """What the transaction page needs to name and explain a finding."""
    types = {t: {k: i[k] for k in ("name", "what", "how", "filter", "option", "note", "staged", "case") if k in i} | {"prs": i.get("prs", [])}
             for t, i in TYPES.items()}
    return {"plumb": PLUMB_NAME, "verdicts": VERDICT_NAMES, "rules": RULES, "sources": {**KNOTS_SOURCE, **FILTER_SOURCE},
            "plumb_options": sorted(PLUMB_OPTIONS), "plumb_own": sorted(PLUMB_OWN), "knots_rules": list(KNOTS_RULES),
            "recount": [RECOUNT, RECOUNT_URL],
            "knots_source": KNOTS_SOURCE, "dcsize": DATACARRIER_SIZE, "types": types,
            "limits": {"dcsize": DATACARRIER_SIZE, "mss": MAXSCRIPTSIZE_DEFAULT, "dust": DUSTRELAYFEE_DEFAULT,
                       "mss_floor": MIN_USEFUL_SCRIPT_LIMIT, "dust_max": MAX_DUST_RATE,
                       "bpss": BYTESPERSIGOPSTRICT_DEFAULT, "bpss_max": MAX_USEFUL_SIGOP_STRICT},
            "filters": {"knots": KNOTS, "plumb": PLUMB, "none": NONE, "allowed": ALLOWED, "notspam": NOT_SPAM},
            "known": {k: {"name": a, "what": b} for k, (a, b) in KNOWN.items()}}


# ---------------------------------------------------------------- other pages

def shame_tables(idx, key, seconds, min_blocks):
    blocks = window_blocks(idx, seconds)
    ps = pool_stats(blocks)
    ranked = sorted([p for p in ps.values() if p["blocks"] >= min_blocks and p["sn"]], key=lambda p: -p["share"])
    honor = sorted([p for p in ps.values() if p["blocks"] >= min_blocks and not p["sn"]], key=lambda p: -p["blocks"])
    small = sorted([p for p in ps.values() if p["blocks"] < min_blocks and p["sn"]], key=lambda p: -p["sw"])
    return blocks, ps, ranked, honor, small


def shame_pane(idx, key, label, seconds, min_blocks):
    blocks, ps, ranked, honor, small = shame_tables(idx, key, seconds, min_blocks)
    tot = totals(blocks)
    pod = []
    for i, p in enumerate(ranked[:3]):
        pod.append(f'''<div class="panel"><div class="place">{["Worst", "Second", "Third"][i]} · {label}</div>
<div class="name">{pool_link(p["pool"])}</div><div class="big g-raw">{pct(p["share"])}</div>
<div class="small muted">of its block space went to sewage. {n(p["sn"])} transactions in {n(p["dirty"])} of {n(p["blocks"])} blocks,
{btc(p["sf"])} BTC in fees taken for them.</div><div style="margin-top:10px"><span class="letter sm gr-{p["letter"]}">{p["letter"]}</span> <span class="small muted">{LETTER_TEXT[p["letter"]]}</span></div></div>''')
    mx = max([p["share"] for p in ranked] or [0])
    rows = []
    for i, p in enumerate(ranked):
        worst = p["worst"]
        rows.append(f'''<tr class="{"rank1" if i == 0 else ""}"><td class="rank">{i + 1}</td><td>{pool_link(p["pool"])}</td>
<td><span class="letter sm gr-{p["letter"]}">{p["letter"]}</span></td>
<td>{barcell(p["share"], mx)}<span class="num">{pct(p["share"], 2)}</span></td>
<td class="r num">{n(p["sn"])}</td><td class="r num">{n(p["dirty"])} / {n(p["blocks"])}</td>
<td class="r num">{size(p["sd"])}</td><td class="r num">{btc(p["sf"])}</td>
<td class="r num">{pct(p["fee_share"])}</td>
<td>{f'<a href="/block/{worst["h"]}/">{worst["h"]}</a> <span class="faint">({pct(worst["share"])})</span>' if worst else ""}</td></tr>''')
    table = f'''<div class="tw"><table><tr><th>#</th><th>Pool</th><th>Grade</th><th>Sewage share of block space</th><th class="r">Sewage txs</th>
<th class="r">Dirty blocks</th><th class="r">Payload</th><th class="r">Spam fees BTC</th><th class="r">Of fee income</th><th>Worst block</th></tr>{"".join(rows)}</table></div>'''
    worst_blocks = sorted([b for b in blocks if b["sn"]], key=lambda b: -b["sd"])[:10]
    wb = "".join(f'<tr><td><a href="/block/{b["h"]}/">{b["h"]}</a></td><td>{pool_link(who(b))}</td><td>{stamp(b["grade"], True)}</td>'
                 f'<td class="r num">{pct(b["share"])}</td><td class="r num">{n(b["sn"])}</td><td class="r num">{size(b["sd"])}</td>'
                 f'<td>{esc(info(b["top"][1])["name"]) if b["top"] else ""}</td></tr>' for b in worst_blocks)
    hon = "".join(f'<tr><td>{pool_link(p["pool"])}</td><td class="r num">{n(p["blocks"])}</td></tr>' for p in honor)
    sm = ""
    if small:
        sm = ('<p class="small muted" style="margin-top:12px">Also mined sewage, with too few blocks to rank: ' +
              ", ".join(f'{pool_link(p["pool"])} ({n(p["sn"])} in {n(p["blocks"])} block{"s" if p["blocks"] != 1 else ""})' for p in small[:30]) + ".</p>")
    return f'''<div class="tabpane{" on" if key == "7d" else ""}" id="w-{key}" data-group="shame">
<div class="tiles"><div class="tile"><div class="v g-raw">{pct(tot["share"], 2)}</div><div class="l">of all block space was sewage</div></div>
<div class="tile"><div class="v">{n(tot["sn"])}</div><div class="l">sewage transactions mined</div></div>
<div class="tile"><div class="v">{btc(tot["sf"])}</div><div class="l">BTC paid to miners to carry it</div></div>
<div class="tile"><div class="v">{n(tot["clean"])} <span class="muted" style="font-size:1rem">/ {n(tot["blocks"])}</span></div><div class="l">blocks with no sewage</div></div></div>
<div class="podium">{"".join(pod) or '<div class="panel">No pool with enough blocks mined sewage in this window.</div>'}</div>
<h3>Every pool that mined sewage <span class="muted small">(at least {min_blocks} blocks in the window)</span></h3>
{table if ranked else '<p class="muted">Nobody, in this window.</p>'}{sm}
<div class="grid2 lead" style="margin-top:26px"><div><h3>Biggest dumps</h3><div class="tw"><table><tr><th>Block</th><th>Pool</th><th>Grade</th><th class="r">Share</th><th class="r">Txs</th><th class="r">Payload</th><th>Main kind</th></tr>{wb}</table></div></div>
<div class="honor"><h3>Clean water roll</h3><p class="small muted" style="margin-bottom:8px">Pools with {min_blocks} or more blocks in the window and not one sewage transaction.</p>
<div class="tw"><table><tr><th>Pool</th><th class="r">Blocks</th></tr>{hon or '<tr><td colspan="2" class="muted">Nobody qualified.</td></tr>'}</table></div></div></div>
</div>'''


def shame_page(idx, tip):
    tabs = "".join(f'<button data-pane="w-{k}"{" class=on" if k == "7d" else ""}>{l}</button>' for k, l, _, _ in WINDOWS)
    panes = "".join(shame_pane(idx, k, l, s, m) for k, l, s, m in WINDOWS)
    body = f'''<div class="hero"><div class="kicker">Hall of Shame</div><h1>Who is mining the sewage</h1>
<p class="lede">Pools ranked by how much of their block space went to spam. A pool lands here by choosing what goes into its blocks, and it gets off the list the same way.</p>
<p class="small muted" style="margin-top:8px">Blocks holding transactions that Knots and Plumb refuse at their defaults are listed on <a href="/past-defaults/">Past the defaults</a>.</p></div>
<div class="tabs" data-group="shame">{tabs}</div>{panes}
<p class="small muted" style="margin-top:22px">Grades: A under 0.05% of block space is sewage, B under 0.25%, C under 1%, D under 4%, F above that.
{DATUM_NOTE}</p>
{plumb_cta("Every pool on this page can leave it with one binary swap.")}'''
    return page("/shame/", "Hall of Shame", body, nav="shame", desc="Pools ranked by how much of their block space went to spam.", og="/og/shame.png", tip=tip)


# The recommended filter settings for Knots 29.4.3, written out, and Plumb's four on top. Every value is
# the default except datacarriersize, datacarriercost and bytespersigopstrict (see conf_stricter).
# rejectdeadbranches and rejectbpub came into Knots with 29.4.3 (knots#400, #463); the bare-envelope and
# unproven-key rules came in too (knots#319, #422) but with no switch, so only Plumb's files name them. They cover the filters on Knots' Spam filtering tab
# (src/qt/optionsdialog.cpp) other than its fee, dust, rate-limit, package-size, unknown-witness and
# bare-anchor settings, plus datacarrier, datacarrierfullcount and corepolicy. Tested on
# regtest: each binary starts with its file and logs every line.
SETTINGS_KNOTS = ("corepolicy=0", "acceptnonstdtxn=0", "rejecttokens=1", "rejectparasites=1", "rejectdeadbranches=1", "rejectbpub=1",
                  "subdustfeepenalty=1", "datacarrier=1", "datacarriersize=42", "datacarrierfullcount=1", "datacarriercost=2",
                  "acceptnonstddatacarrier=0", "permitbaredatacarrier=0", "permitbarepubkey=0", "permitbaremultisig=0",
                  "permitephemeral=anchor,-send,-dust", "maxscriptsize=1650", "maxtxlegacysigops=2500",
                  "bytespersigop=20", "bytespersigopstrict=51")
SETTINGS_PLUMB = ("rejectfakeoutputs=1", "rejectbareenvelopes=1", "rejectfakemultisig=1", "rejecttokenmessages=1")
# Plumb switches for rules Knots 29.4.3 applies with no switch of its own
KNOTS_HAS_NO_SWITCH = {"-rejectbareenvelopes", "-rejectfakemultisig"}
# The data-counting rules the engine measures one at a time ("fc"), split by whose they are: Knots 29.4.3
# applies the first four by default (two of them with no switch, which Plumb adds), Plumb alone the last.
KNOTS_RULES = ("-rejectdeadbranches", "-rejectbareenvelopes", "-rejectfakemultisig", "-rejectbpub")
PLUMB_OWN = {"-rejectfakeoutputs", "-rejecttokenmessages"}
# The engine also measures knots#470's recount on its own (fc "knots#470": what Plumb counts from the keys a
# signature verified against beyond the count from the script alone). Knots 29.4.3 has no recount, so those bytes
# are Plumb's own, under its -rejectfakemultisig switch, and Plumb's -rejectfakemultisig count includes them.
RECOUNT = "knots#470"
RECOUNT_URL = "https://github.com/bitcoinknots/bitcoin/pull/470"
KNOTS_SOURCE = {"-rejectdeadbranches": ("knots#400", "https://github.com/bitcoinknots/bitcoin/pull/400"),
                "-rejectbareenvelopes": ("knots#319", "https://github.com/bitcoinknots/bitcoin/pull/319"),
                "-rejectfakemultisig": ("knots#422", "https://github.com/bitcoinknots/bitcoin/pull/422"),
                "-rejectbpub": ("knots#463", "https://github.com/bitcoinknots/bitcoin/pull/463")}


def load_filters():
    """Plumb's filter manifest, from the Plumb checkout when it is there."""
    try:
        with open(FILTERS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


PLUMB_FILTERS = load_filters()
# Each Plumb option with its source and link: the manifest, else the field guide's PR links.
FILTER_SOURCE = ({x["option"]: (x["source"], x["url"]) for x in PLUMB_FILTERS}
                 or {i["option"]: i["prs"][0] for i in TYPES.values() if i.get("filter") == PLUMB and i.get("prs")})
PLUMB_OPTIONS = set(FILTER_SOURCE) | {"-" + x.split("=")[0] for x in SETTINGS_PLUMB}
SETTINGS_GREP = 'grep -E "Bitcoin (Knots|Core) version|Using data directory|Plumb filter|arg: (\\[[a-z0-9]+\\] )?(corepolicy|reject|datacarrier|acceptnonstd|permitbare|permitlnrevokeleaf|maxscriptsize|bytespersigopstrict)"'
SETTINGS_CHECK = SETTINGS_GREP + " ~/.bitcoin/debug.log"
UMBREL_LOG = "~/umbrel/app-data/bitcoin-knots/data/bitcoin/debug.log"
LOWER_LIMITS = f"{PLUMB_REPO}/blob/29.x-plumb/plumb/FILTERS.md#lower-data-limits"
SETTINGS_LINK = '<a href="/check/">Settings that keep them out</a>'


# The numbers the recommended files quote, counted over the per-transaction records in TXD for
# blocks 961640 to 976619 by tools/conf_facts.py, graded with Plumb's 42-byte default. Knots refuses a
# transaction when it gives a reason, and at datacarriersize=42 also when its fix.knots.dcs is 42 or
# more; "gain" is the sewage 42 adds for Knots, by the record's primary type (no transaction that is not
# sewage is added). plumb_only42: the sewage Plumb refuses only for being over 42, its p83 empty, counted
# on the block records. Fixed, so the files read the same every block.
CONF_FACTS = {"lo": 961640, "hi": 976619, "txs": 3569710, "sewage": 109247, "knots_sewage": 25786,
              "knots42_sewage": 107654, "plumb_sewage": 109247, "plumb_only42": 80988,
              "gain": {"swap-memo": 41780, "opreturn-text": 19791, "opreturn-data": 16728, "stacks": 1458,
                       "babylon": 1158, "token-json": 859, "coredao": 83, "bip47": 11}}
# The -bytespersigopstrict and -maxscriptsize numbers, same blocks, from tools/sigop_facts.py 961640 976619,
# which counts every transaction, not only the ones the site shows. fake: sewage listed as fake multisig;
# fake_knots of them Knots 29.4.3 refuses at the defaults (knots#422 and #463), fake_plumb Plumb; fake_value
# would be refused by bytespersigopstrict=51 on its own, fake_upto55 more at 52 to 55, fake_over only above
# 55. other: what 51 refuses that is not sewage; nearest: the smallest value that refuses anything not sewage.
# knots42_relays: the sewage Knots relays at datacarriersize=42. mss_*: what maxscriptsize=590 refuses that
# Knots relays, the rest by the script they match; largest: the biggest script or witness among the
# transactions not listed as sewage.
SIGOP_FACTS = {"value": 51, "fake": 700, "fake_knots": 700, "fake_plumb": 700, "fake_value": 675, "fake_upto55": 21, "fake_over": 4,
               "other": 0, "nearest": 56, "knots42_relays": 1593,
               "mss": 590, "mss_sewage": 0, "mss_other": 552, "mss_babylon": 386, "mss_liquid": 44, "mss_rsk": 22,
               "largest": 1609}
# What the defaults refuse that cesspool.lol does not count as sewage, same blocks, counted on the block
# records by primary type: sweeps and Flyover spends, and the one Liquid-layout emergency spend
# rejectfakemultisig refuses (it stays sewage). Plumb 7 no longer refuses the payment back to a vanity
# address that Plumb 6 did.
LEGIT_FACTS = {"sweeps": 279, "flyover": 5, "liquid": 1}
# The aggressive Knots file: the recommended one with dustrelayfee=0.00005 and bytespersigopstrict=55. Same
# blocks, from tools/aggressive_facts.py 961640 976619 5000 55, which counts every transaction against what the
# recommended Knots file relays: no filter refuses it and it pays Knots' minimum relay fee on the vsize Knots
# gives it at datacarriercost=2. sewage: the sewage that file relays, sewage_w its weight; left: what this file
# still relays; dust_sewage: what 0.00005 refuses of it, strict_sewage what 55 refuses beyond that; run: the
# p2wsh-run transactions, all of them in those blocks and all relayed by that file, run_w their weight, run_546
# those with 546-sat P2WSH outputs, run_median such outputs to a transaction; other: what that file relays that
# is not sewage; dust_other: what 0.00005 refuses of that, ln of them Lightning force closes out of the ln_all
# that file relays (dust_other_kinds in the tool's output names the outputs that make the rest dust); fees: what
# everything this file refuses beyond that file paid, in sats. fake_value: the fake multisig refused at 55, from
# tools/sigop_facts.py 961640 976619 55, which also finds the one Liquid-layout spend (liquid, under Knots' fee
# floor at 0.6 sat/vB) and nothing else. dust_exact: the lowest rate that makes a 546-sat P2WSH output dust;
# p2wsh to p2pkh: the dust threshold at 0.00005.
AGGR_FACTS = {"dust": "0.00005", "dust_exact": "0.00004964", "value": 55, "sewage": 1205, "left": 1,
              "dust_sewage": 1204, "strict_sewage": 0, "run": 713, "run_546": 711, "run_median": 256,
              "run_w": 30932788, "sewage_w": 32076980, "other": 3355612, "dust_other": 69174, "ln": 16043,
              "ln_all": 19197, "fees": 72198518, "fake_value": 696, "liquid": 0, "strict_other": 0,
              "p2wsh": 550, "p2tr": 550, "p2wpkh": 490, "p2sh": 900, "p2pkh": 910}
CONF_NAMES = {"knots": "Bitcoin " + KNOTS_NAME, "plumb": PLUMB_NAME}
# The Lightning files: the recommended ones with three settings changed. From tools/lightning_facts.py 961640
# 978047, which counts every transaction in the cached blocks. txs: transactions, not counting coinbases;
# p2a_under_240: those with a pay-to-anchor output under 240 sat; v3_sub_floor: version 3 transactions paying
# less than their plain vsize in sat, so under the 1 sat/vB floor on any sizing, v3_zero_fee of them paying
# nothing, v3_sub_floor_relayed those with a version 3 child in the same block paying 1 sat/vB for the pair,
# the only way such a parent gets through package relay; ln: Lightning force closes (locktime's top byte 0x20,
# every sequence's top byte 0x80), ln_anchor_pair those with two 330-sat P2WSH outputs, ln_run_hit those with
# three or more distinct P2WSH outputs sharing one value of 546 sat or less, which -rejectfakeoutputs
# refuses; ln_taproot_revoke: those spending LND's simple taproot channel revocation leaf through the script path
# (knots#487); ln_taproot_revoke_token of them carry a JSON token message that Plumb refuses as tokens-json
# (counted in the plumb9 regrade's logs/attribution.tsv, from tools/attribute.py, reviews-1029/cesspool-plumb9).
# The weight share of the P2WSH runs comes from AGGR_FACTS.
LN_FACTS = {"lo": 961640, "hi": 978047, "txs": 3878328, "p2a_under_240": 0, "v3_sub_floor": 37, "v3_zero_fee": 37,
            "v3_sub_floor_relayed": 0, "ln": 19448, "ln_anchor_pair": 6839, "ln_run_hit": 0,
            "ln_taproot_revoke": 223, "ln_taproot_revoke_token": 223}


def conf_comment(*paras):
    """Paragraphs as bitcoin.conf comment lines, 72 columns, a bare # between paragraphs."""
    out = []
    for para in paras:
        out += (["#"] if out else []) + textwrap.wrap(para, 70, break_on_hyphens=False, break_long_words=False)
    return [line if line == "#" else "# " + line for line in out]


def conf_stricter(prog):
    """The comment above each value that differs from the default, and above maxscriptsize, by option."""
    f, g, k = CONF_FACTS, SIGOP_FACTS, LEGIT_FACTS
    fake_who = (f"Knots 29.4.3 refuses all {n(g['fake_knots'])} at the defaults, counting the keys no signature "
                "uses (knots#422) and refusing the BPUB file format outright (knots#463)" if prog == "knots"
                else f"rejectfakemultisig and Knots' own rules refuse all {n(g['fake_plumb'])} of them already")
    gain = f["gain"]
    listed = (f"From block {f['lo']} to {f['hi']} ({n(f['txs'])} transactions, not counting coinbases), "
              f"cesspool.lol lists {n(f['sewage'])} as sewage, graded at {PLUMB_NAME}'s defaults.")
    if prog == "knots":
        size = conf_comment(
            f"The default is {DATACARRIER_SIZE['knots']}; Plumb's is {DATACARRIER_SIZE['plumb']}. {listed} With the "
            f"other options at their defaults, Knots refuses {n(f['knots_sewage'])} of them at 83 and "
            f"{n(f['knots42_sewage'])} at 42. The {n(f['knots42_sewage'] - f['knots_sewage'])} that 42 adds are "
            f"{n(gain['swap-memo'])} swap memos, {n(gain['opreturn-text'] + gain['opreturn-data'])} text or binary "
            f"notes, {n(gain['stacks'])} Stacks commitments, {n(gain['babylon'])} Babylon and {n(gain['coredao'])} "
            f"Core DAO staking tags, {n(gain['token-json'])} JSON token messages and {n(gain['bip47'])} BIP47 "
            "notifications. 42 refuses nothing else in those blocks, and keeps 32-byte commitments such as "
            "OpenTimestamps.")
    else:
        size = conf_comment(
            f"Plumb's default since plumb7; Knots' is {DATACARRIER_SIZE['knots']}. Written out so that a corepolicy=1 "
            f"set elsewhere does not raise it to {DATACARRIER_SIZE['knots']}. {listed} Plumb refuses "
            f"{n(f['plumb_sewage'])} of them, and would relay {n(f['plumb_only42'])} of those at 83. 42 keeps "
            "32-byte commitments such as OpenTimestamps.")
    plumb_only = {} if prog == "knots" else {
        "rejectfakemultisig": conf_comment(
            f"In the same blocks it also refuses {n(k['liquid'])} Liquid-layout emergency spend, very likely "
            "Liquid's own recovery. The layout cannot prove that the unsigned keys are real, so cesspool.lol still "
            "counts it as sewage."),
    }
    return plumb_only | {
        "datacarriersize": size,
        "acceptnonstddatacarrier": conf_comment(
            "The default. It refuses data outside OP_RETURN, which includes a push that a script drops unused. "
            f"In the same blocks it also refuses {n(k['flyover'])} Rootstock PowPeg spends of Flyover deposits, "
            "whose scripts start with a 32-byte value dropped that way. cesspool.lol does not count those as "
            f"sewage: {SITE}/about/#bar."),
        "subdustfeepenalty": conf_comment(
            "The default. With it, a version 3 transaction that pays no fee and carries a pay-to-anchor output of at "
            "most 240 sat is refused even beside a child that pays for both: the package Lightning's zero-fee "
            "commitment channels use. It relays only with subdustfeepenalty=0 and mempooltruc=enforce, plus "
            "permitephemeral=anchor,-send,dust when the anchor holds 1 to 239 sat; the Lightning file at "
            f"{SITE}/check/#lightning sets all three. None was mined from block {LN_FACTS['lo']} to {LN_FACTS['hi']}."),
        "permitbaredatacarrier": conf_comment(
            "The default. It refuses a transaction whose only output is an OP_RETURN. In the same blocks it "
            f"also refuses {n(k['sweeps'])} sweeps of Lightning anchors and other dust into fees, whose OP_RETURN "
            "holds 4 bytes or less of padding. cesspool.lol does not count those as sewage."),
        "datacarriercost": conf_comment(
            "The default is 1. At 2, each byte of data counts as 2 vbytes instead of 1 when the node sizes a "
            "transaction for relay and for the blocks it builds, so data pays twice the fee per byte."),
        "maxscriptsize": conf_comment(
            f"The default, sized for a 15-of-15 multisig spend. A lower value would refuse the fake multisig "
            f"below, but it also refuses every ordinary multisig spend bigger than the value: at {g['mss']} it "
            f"refuses {n(g['mss_sewage'])} sewage transactions and {n(g['mss_other'])} that are not, nearly all "
            f"multisig, some as large as {n(g['largest'])} bytes."),
        "bytespersigopstrict": conf_comment(
            f"The default is 20. Knots counts signature checks in a multisig script: one for each key in segwit, "
            f"four for each key in plain P2SH, 20 for any multisig over 16 keys. It refuses a transaction with "
            f"fewer than this many weight units for each check. In segwit each signature adds about 72, so a 2-of-3 P2WSH "
            f"spend has about 139 for each check and a segwit 2-of-15 about 55. Fake multisig, one signature for "
            f"15 keys of which 14 are data, comes in at about 50. Other multisig with many keys and few "
            f"signatures can fall below 51 too, such as a segwit 2-of-17 spending many coins; none did in these "
            f"blocks. In the same blocks cesspool.lol lists {n(g['fake'])} fake multisig "
            f"transactions, and {fake_who}. 51 would refuse {n(g['fake_value'])} of them on its own and nothing "
            f"else; nothing that is not sewage is refused below {g['nearest']}. It stays as a second check that "
            "depends on neither the key count nor the file format: a reveal with ten or fewer unused keys in each "
            f"script and no BPUB header passes both Knots rules. {SITE}/check/#fake-multisig says more."),
    }


def recommended_conf(prog):
    """The recommended bitcoin.conf for "knots" or "plumb", served at /check/<prog>.conf."""
    name = "Knots" if prog == "knots" else "Plumb"
    above = ("Three are stricter than the defaults, datacarriersize, datacarriercost and bytespersigopstrict"
             if prog == "knots" else "Two are stricter than the defaults, datacarriercost and bytespersigopstrict")
    intro = (f"{name}'s spam filter settings, written out so that a corepolicy=1 set elsewhere can no longer "
             f"reset them. {above}; the comment above each says what it does. The comments above some of the "
             "defaults say what they refuse that is not spam. ")
    if prog == "knots":
        intro = intro.replace("Knots's", "Knots'") + ("Knots' Spam filtering tab also has fee, dust, rate-limit, "
                                                       "package-size, unknown-witness and bare-anchor "
                                                       "settings; this file leaves those alone. A corepolicy=1 set elsewhere still changes settings "
                                                       "this file does not set, such as the minimum relay fee.")
        files = ("settings.json and bitcoin_rw.conf in the data directory, where the Knots GUI saves its "
                 "settings, win over this file, as does the command line.")
    else:
        intro += ("The last four are Plumb's own; the rest match the Knots file, and rejectdeadbranches and "
                  "rejectbpub are Knots' own since 29.4.3. Fee, dust, rate-limit, "
                  "package-size, unknown-witness and bare-anchor settings are left alone. A corepolicy=1 set elsewhere "
                  "still changes settings this file does not set, such as the minimum relay fee.")
        files = "settings.json and bitcoin_rw.conf in the data directory win over this file, as does the command line."
    lines = [f"# Recommended bitcoin.conf for {CONF_NAMES[prog]}", f"# {SITE}/check/", "#"] + conf_comment(
        intro,
        "If your bitcoin.conf already sets one of these options, or turns one off with a no line such as "
        "nodatacarrier=1, change or remove that line rather than adding a second one: with two lines, which "
        f"one wins depends on their order and form. A line under [main] wins over lines outside it. {files} "
        "Restart the node after editing.") + [""]
    stricter = conf_stricter(prog)
    for opt in SETTINGS_KNOTS + (SETTINGS_PLUMB if prog == "plumb" else ()):
        lines += stricter.get(opt.split("=")[0], []) + [opt]
    return "\n".join(lines) + "\n"


def aggressive_conf():
    """The aggressive Knots file, served at /check/knots-aggressive.conf: the recommended Knots file with
    dustrelayfee and bytespersigopstrict raised, and what each refuses on both sides."""
    f, g, a = CONF_FACTS, SIGOP_FACTS, AGGR_FACTS
    comments = conf_stricter("knots") | {
        "bytespersigopstrict": conf_comment(
            f"The default is 20; the recommended file sets {g['value']}. Knots counts signature checks in a multisig "
            "script: one for each key in segwit, four for each key in plain P2SH, 20 for any multisig over 16 keys. "
            "It refuses a transaction with fewer than this many weight units for each check. Fake multisig, one "
            "signature for 15 keys of which 14 are data, comes in at about 50. In the same blocks cesspool.lol "
            f"lists {n(g['fake'])} fake multisig transactions, and Knots 29.4.3 refuses all of them at the "
            f"defaults (knots#422, #463). On its own {g['value']} would refuse {n(g['fake_value'])} of them and "
            f"{a['value']} {n(a['fake_value'])}; beyond the recommended file {a['value']} refuses "
            f"{n(a['strict_sewage'])} sewage transactions and {n(a['strict_other'])} that are not. Nothing that is "
            f"not sewage is refused below {g['nearest']}. Other multisig with many "
            f"keys and few signatures can fall between {g['value']} and {a['value']}: on the 29.4.2 release "
            f"binary, {a['value']} refused, and {g['value']} let through, a segwit 1-of-14 spend of 200 coins, a "
            "segwit 2-of-16 spend of 100 coins and plain P2SH 2-of-12, 2-of-13 and 2-of-14 spends of one coin. "
            "Where the line falls depends on the keys, signatures, coins and outputs. A segwit 2-of-15 spend still "
            "relays: "
            f"one of 200 coins comes in at about 55.2 and one of a single coin at about 69. "
            f"{SITE}/check/#fake-multisig says more."),
        "dustrelayfee": conf_comment(
            "The default is 0.00003, which is 3 sats a vbyte, and this file sets 5. Knots refuses a transaction "
            "that creates dust, an output worth less than the fee to spend it at this rate: at "
            f"{a['dust']}, an output under {a['p2wsh']} sats to a P2WSH or taproot address, under {a['p2wpkh']} "
            f"to P2WPKH, under {a['p2sh']} to P2SH and under {a['p2pkh']} to a legacy address. In the same "
            f"blocks, {n(a['run'])} transactions set up runs of P2WSH outputs for data that lands on chain when "
            "the outputs are spent, mostly as fake multisig keys. They are "
            f"{pct(a['run_w'] / a['sewage_w'], 0)} of the block weight of the sewage the recommended file "
            f"relays. {n(a['run_546'])} of them pay 546 sats to such outputs, a median of {a['run_median']} to a "
            f"transaction, and a 546-sat P2WSH output is dust from {a['dust_exact']} up. This value refuses "
            f"{n(a['dust_sewage'])} of the {n(a['sewage'])} sewage transactions the recommended file relays, "
            f"all {n(a['run'])} of those among them. It also refuses {n(a['dust_other'])} that are not sewage: "
            f"{n(a['ln'])} Lightning force closes and {n(a['dust_other'] - a['ln'])} others that create small "
            "outputs, most often 546 sats to a taproot address or 294 to a P2WPKH one. A wallet that sends "
            "through this node has a payment or change output that small refused the same way."),
    }
    lines = [f"# Aggressive bitcoin.conf for {CONF_NAMES['knots']}",
             "# Not the recommended file: it refuses ordinary transactions too.", f"# {SITE}/check/#aggressive", "#"]
    lines += conf_comment(
        "This is the recommended Knots file, which already sets datacarriersize, datacarriercost and "
        f"bytespersigopstrict stricter than the defaults, with bytespersigopstrict raised from {g['value']} to "
        f"{a['value']} and dustrelayfee from 0.00003 to {a['dust']}. From block {f['lo']} to {f['hi']}, Knots "
        f"with the recommended file relays {n(a['sewage'])} of the {n(f['sewage'])} transactions cesspool.lol "
        f"lists as sewage; with this file it relays {n(a['left'])}. It also refuses {n(a['dust_other'])} "
        "transactions that the recommended file relays and that are not sewage, "
        f"{pct(a['dust_other'] / a['other'])} of them. A transaction counts as relayed here when no filter in "
        "the file refuses it and it pays Knots' minimum relay fee. The comment above each of the two values "
        "says what it refuses. A node that builds blocks leaves the same transactions out of them, and the "
        f"{a['fees'] / 1e8:.2f} BTC in fees they paid in those blocks.",
        "Do not use this file on a node that a Lightning node sends transactions through, directly or through "
        "an Electrum server. At this dustrelayfee Knots refuses a transaction that creates a 330-sat anchor "
        "output, as force closes of anchor channels do, even beside a child that pays its fee. A Lightning "
        "node behind it cannot force close an anchor channel, and a force close that misses its deadline can "
        "lose "
        f"the payments in flight in that channel. In those blocks this file refuses {n(a['ln'])} of the "
        f"{n(a['ln_all'])} Lightning force closes the recommended file relays.",
        "Knots' spam filter settings are written out so that a corepolicy=1 set elsewhere can no longer reset "
        "them. The comments above some of the defaults say what they refuse that is not spam. Knots' Spam "
        "filtering tab also has fee, dynamic dust, rate-limit, package-size, unknown-witness and bare-anchor "
        "settings; this file leaves those alone. A corepolicy=1 set elsewhere still changes settings this file "
        "does not set, such as the minimum relay fee.",
        "If your bitcoin.conf already sets one of these options, or turns one off with a no line such as "
        "nodatacarrier=1, change or remove that line rather than adding a second one: with two lines, which "
        "one wins depends on their order and form. A line under [main] wins over lines outside it. "
        "settings.json and bitcoin_rw.conf in the data directory, where the Knots GUI saves its settings, win "
        "over this file, as does the command line. Restart the node after editing.") + [""]
    settings = tuple(o if o != f"bytespersigopstrict={g['value']}" else f"bytespersigopstrict={a['value']}"
                     for o in SETTINGS_KNOTS) + (f"dustrelayfee={a['dust']}",)
    for opt in settings:
        lines += comments.get(opt.split("=")[0], []) + [opt]
    return "\n".join(lines) + "\n"


def lightning_conf(prog):
    """The Lightning file for "knots" or "plumb", served at /check/<prog>-lightning.conf: the recommended file
    with the three settings a zero-fee commitment channel needs, the dust fee settings written out at their
    defaults, and what the change lets through, from LN_FACTS."""
    a, l = AGGR_FACTS, LN_FACTS
    name = "Knots" if prog == "knots" else "Plumb"
    comments = conf_stricter(prog) | {
        "subdustfeepenalty": conf_comment(
            "The Knots default is 1, which refuses the zero-fee commitment package described above. Off, a "
            "pay-to-anchor output under its dust limit no longer counts against the fee of the transaction that "
            "creates it. It is the only dust output Knots permits at all: permitephemeral below keeps every other "
            "dust output refused, with or without this line."),
        "mempooltruc": conf_comment(
            "The Knots default is accept, which treats a version 3 transaction like any other and holds it to the "
            "relay floor on its own. enforce is the Core default: a version 3 parent may pay nothing when its child "
            "pays for both, and in return it gets at most one unconfirmed parent or child, 10,000 vbytes for the "
            "parent and 1,000 for the child."),
        "permitephemeral": conf_comment(
            "The Knots default is anchor,-send,-dust: the only dust output it permits is a pay-to-anchor worth "
            "exactly zero. dust adds a pay-to-anchor holding less than its dust limit, which a zero-fee commitment "
            "carries when the channel trimmed a small output into it. send stays off, so no other output type is "
            "relayed as dust. The transaction must still pay no fee of its own, and the child must still spend the "
            "anchor."),
        "dustrelayfee": conf_comment(
            "The defaults, written out. Anything above 0.00003 makes 330-sat anchors dust; see the note at the top."),
    }
    if prog == "plumb":
        comments["rejectfakeoutputs"] = conf_comment(
            "Three or more P2WSH outputs in one transaction sharing one value of 546 sat or less count as data, "
            "which refuses a force close whose commitment carries three or more HTLC outputs of the same value "
            f"between 354 sat, the floor channels trim HTLCs under, and 546. None of the {n(l['ln'])} force closes "
            f"mined from block {l['lo']} to {l['hi']} carried three or more P2WSH outputs of one value of 546 sat or "
            "less. Set rejectfakeoutputs=0 to remove the case; "
            "the node then relays everything that filter catches, among it the runs of small P2WSH outputs that "
            f"stage data for later reveals, {pct(a['run_w'] / a['sewage_w'], 0)} by weight of the sewage the "
            "recommended Knots file relays.")
    apart = ("apart from the OP_RETURN close and the LND penalty spend noted below" if prog == "knots" else
             "apart from the OP_RETURN close noted below and the one case noted above rejectfakeoutputs at the end "
             "of this file")
    # knots#487: what each program does with the penalty spend of an LND simple taproot channel
    leaf_bytes = 34
    spent = (f"From block {l['lo']} to {l['hi']}, " + (plural(l["ln_taproot_revoke"], "mined transaction") + " spent"
             if l["ln_taproot_revoke"] else "no mined transaction spent") + " a leaf of that form"
             + (", every one of them carrying a JSON token message." if l["ln_taproot_revoke"] and l["ln_taproot_revoke_token"] == l["ln_taproot_revoke"] else "."))
    if prog == "knots":
        lnd = (f"With this file Knots {KNOTS_NAME.split()[-1]} refuses the penalty spend of an LND simple taproot "
               f"channel: the channel's revocation leaf pushes a 32-byte key and drops it, and Knots counts those "
               f"{leaf_bytes} bytes as non-standard data. Plumb relays it since plumb9 (knots#487). {spent}")
    else:
        lnd = (f"Plumb relays the penalty spend of an LND simple taproot channel, counting the {leaf_bytes} bytes its "
               f"revocation leaf pushes and drops toward datacarriersize (knots#487; permitlnrevokeleaf=0 turns this "
               f"off). One such input fits {DATACARRIER_SIZE['plumb']}; two in one transaction count {2 * leaf_bytes} "
               f"and are refused. Knots {KNOTS_NAME.split()[-1]} refuses the spend. {spent}")
    if prog == "knots":
        own = ("Knots' spam filter settings are written out so that a corepolicy=1 set elsewhere can no longer reset "
               "them. The comments above some of the defaults say what they refuse that is not spam. Knots' Spam "
               "filtering tab also has fee, rate-limit, package-size, unknown-witness and bare-anchor settings; this "
               "file leaves those alone. A corepolicy=1 set elsewhere still changes settings this file does not set, "
               "such as the minimum relay fee.")
        files = ("settings.json and bitcoin_rw.conf in the data directory, where the Knots GUI saves its settings, "
                 "win over this file, as does the command line.")
    else:
        own = ("Plumb's spam filter settings are written out so that a corepolicy=1 set elsewhere can no longer reset "
               "them. The comments above some of the defaults say what they refuse that is not spam. The last four "
                  "are Plumb's own and rejectdeadbranches and rejectbpub are Knots' own since 29.4.3; the rest match the Knots file. Fee, rate-limit, package-size, unknown-witness and "
               "bare-anchor settings are left alone. A corepolicy=1 set elsewhere still changes settings this file "
               "does not set, such as the minimum relay fee.")
        files = "settings.json and bitcoin_rw.conf in the data directory win over this file, as does the command line."
    lines = [f"# bitcoin.conf for {CONF_NAMES[prog]} behind a Lightning node", f"# {SITE}/check/", "#"] + conf_comment(
        f"The recommended {name} file with three lines changed, subdustfeepenalty=0, mempooltruc=enforce and "
        "permitephemeral=anchor,-send,dust, and the dust fee settings written out at their defaults. A Lightning "
        "node that opens zero-fee commitment channels needs the three lines: LDK 0.2 and later can open them "
        "between LDK peers, eclair 0.14 has them as an option that is off by default, and LND 0.21 and CLN 26.06 "
        "show no sign of them. Everything else Lightning sends, force closes of anchor channels with their 330-sat "
        "anchors, the fee bump that spends an anchor, HTLC-timeout spends and mutual closes, already relays with "
        f"the recommended file, {apart}, so a node that does not open those channels does not need this one.",
        "The commitment transaction of a zero-fee commitment channel is version 3, pays no fee itself beyond a "
        "trimmed remainder, and carries one pay-to-anchor output of at most 240 sat, zero unless a trimmed output "
        "or rounding leaves something in it; a child spends the anchor and pays for both. Knots' default "
        "subdustfeepenalty=1 takes the anchor's dust shortfall, 240 sat for an empty anchor, off the parent's fee, "
        "which must be exactly zero for a transaction with a dust output, so the parent is refused: \"dust, tx with "
        "dust output must be 0-fee\". With the penalty off, mempooltruc=accept still holds the parent to the 1 "
        "sat/vB relay floor on its own: \"min relay fee not met\". And the default permitephemeral=anchor,-send,-dust "
        "refuses an anchor holding a trimmed amount, 1 to 239 sat: \"dust-nonzero\". All three lines are needed. "
        "Core 29 relays these packages and LDK's 0.2 release notes say a path of Core 29 nodes to a miner is "
        "required; with these three lines Knots relays them too. Two Knots pull requests on the first two "
        "settings, #438 and #447, are open.",
        "What the three lines let through that the recommended file refuses: a version 3 transaction that pays "
        "less than the 1 sat/vB relay floor on its own, nothing or some fee under it, with at most one "
        "pay-to-anchor output under its dust limit, when it arrives with a version 3 child that pays the floor for "
        "both and spends the anchor if there is one. The child pays on the same sizing as any other transaction "
        "here, so data in the parent still costs 2 vbytes a byte, and every other setting in this file applies to "
        "both. TRUC enforcement also caps the parent at 10,000 vbytes and the child at 1,000, with no other "
        "unconfirmed parent or child, and refuses an unconfirmed chain that mixes version 3 with other versions, "
        f"as Core does. From block {l['lo']} to {l['hi']} ({n(l['txs'])} transactions, not counting coinbases) "
        "nothing mined is relayed by this file and refused by the recommended one: no mined transaction had a "
        f"pay-to-anchor output under 240 sat, and the {l['v3_sub_floor']} version 3 transactions mined paying less "
        "than the floor on their own, every one of them paying nothing, each lack a version 3 child paying the "
        "floor for both, so a miner took them directly.",
        "Do not raise dustrelayfee or turn on dustdynamic on this node. Anchor channels put two 330-sat anchors on "
        "every commitment transaction, and any dustrelayfee above the default 0.00003 makes 330 sat dust, and from "
        "there Knots refuses every anchor force close; a force close that misses its deadline can lose the "
        "payments in flight in that channel. A fee-paying child does not help; dust is not a fee failure.",
        f"The datacarriersize of {DATACARRIER_SIZE['plumb']} refuses one Lightning transaction the Knots default of "
        f"{DATACARRIER_SIZE['knots']} relays: a mutual close that burns a balance too small to pay out into an "
        f"OP_RETURN script longer than {DATACARRIER_SIZE['plumb']} bytes, which is a push of more than "
        f"{DATACARRIER_SIZE['plumb'] - 2}. The spec lets a side do that with a push of 6 to 80 bytes; raise the "
        f"value to {DATACARRIER_SIZE['knots']} if your node does.",
        lnd,
        own,
        "If your bitcoin.conf already sets one of these options, or turns one off with a no line such as "
        "nodatacarrier=1, change or remove that line rather than adding a second one: with two lines, which "
        f"one wins depends on their order and form. A line under [main] wins over lines outside it. {files} "
        "Restart the node after editing.") + [""]
    settings = []
    for o in SETTINGS_KNOTS:
        if o == "subdustfeepenalty=1":
            settings += ["subdustfeepenalty=0", "mempooltruc=enforce"]
        elif o == "permitephemeral=anchor,-send,-dust":
            settings.append("permitephemeral=anchor,-send,dust")
        else:
            settings.append(o)
    settings += ["dustrelayfee=0.00003", "dustdynamic=off"] + (list(SETTINGS_PLUMB) if prog == "plumb" else [])
    for opt in settings:
        lines += comments.get(opt.split("=")[0], []) + [opt]
    return "\n".join(lines) + "\n"


CONF_FILES = {"plumb": (lambda: recommended_conf("plumb"), f"Download the {CONF_NAMES['plumb'].split(' 29')[0]} file"),
              "knots": (lambda: recommended_conf("knots"), f"Download the {CONF_NAMES['knots'].split(' 29')[0]} file"),
              "knots-aggressive": (aggressive_conf, "Download the aggressive Knots file"),
              "plumb-lightning": (lambda: lightning_conf("plumb"), "Download the Plumb Lightning file"),
              "knots-lightning": (lambda: lightning_conf("knots"), "Download the Knots Lightning file")}


def conf_pane(key, on):
    make, label = CONF_FILES[key]
    conf = make()
    return f'''<div class="tabpane{" on" if on else ""}" id="conf-{key}" data-group="conf">
<pre class="conf">{esc(conf)}</pre>
<p><a class="btn" href="/check/{key}.conf" download="bitcoin.conf">{esc(label)}</a> <button class="copy" data-copy="{esc(conf)}">Copy it</button></p></div>'''


def aggressive_note():
    f, a = CONF_FACTS, AGGR_FACTS
    return f'''<p class="small" id="aggressive" style="max-width:75ch"><b>The aggressive Knots file is not the recommendation.</b> It is the recommended Knots file with <code>dustrelayfee={a["dust"]}</code> and <code>bytespersigopstrict={a["value"]}</code>, for operators who will refuse some ordinary transactions to refuse more spam. From block {f["lo"]} to {f["hi"]} cesspool.lol lists {n(f["sewage"])} transactions as sewage. Knots relays {n(a["sewage"])} of them with the recommended file and {n(a["left"])} with this one, which also refuses {n(a["dust_other"])} more that are not sewage, {n(a["ln"])} of them Lightning force closes. Do not use it on a node that a Lightning node sends transactions through: the Lightning node cannot force close an anchor channel, and a force close that misses its deadline can lose the payments in flight. Plumb with its own recommended file refuses {n(f["plumb_sewage"])} of the {n(f["sewage"])} without raising either value.</p>'''


def lightning_pane():
    """One pane for both Lightning files, Plumb first."""
    parts = []
    for key, head in (("plumb-lightning", "Plumb"), ("knots-lightning", "Knots")):
        make, label = CONF_FILES[key]
        conf = make()
        parts.append(f'<p class="small" style="margin:10px 0 4px"><b>{head}</b></p>\n<pre class="conf">{esc(conf)}</pre>\n'
                     f'<p><a class="btn" href="/check/{key}.conf" download="bitcoin.conf">{esc(label)}</a> '
                     f'<button class="copy" data-copy="{esc(conf)}">Copy it</button></p>')
    return '<div class="tabpane" id="conf-lightning" data-group="conf">\n' + "\n".join(parts) + "</div>"


def lightning_note():
    l = LN_FACTS
    return f'''<p class="small" id="lightning" style="max-width:75ch"><b>The Lightning files are for a node that a Lightning node sends transactions through.</b> Everything a CLN or LND node broadcasts relays with the recommended files, apart from a mutual close that burns a tiny balance into an OP_RETURN over 42 bytes, on Knots the penalty spend of an LND simple taproot channel, and, on Plumb, the one case at the end of this note: force closes of anchor channels with their 330-sat anchors, the fee bump that spends an anchor, HTLC-timeout spends, mutual closes. The commitment transaction of a zero-fee commitment channel does not: it is version 3, pays no fee itself beyond a trimmed remainder, and carries one pay-to-anchor output of at most 240 sat, with a child paying for both, and three Knots defaults refuse it. The Lightning files are the recommended files with those three changed, <code>subdustfeepenalty=0</code>, <code>mempooltruc=enforce</code> and <code>permitephemeral=anchor,-send,dust</code>, and the dust fee settings written out at their defaults. What the three lines let through: a version 3 transaction that pays less than the 1 sat/vB relay floor on its own, with at most one pay-to-anchor output under its dust limit, when it arrives with a version 3 child that pays the floor for both. Data in it costs what it costs anywhere else in the file. From block {l["lo"]} to {l["hi"]} nothing mined fits that description. LDK 0.2 and later can open these channels between LDK peers, eclair 0.14 has them as an option that is off by default, and LND 0.21 and CLN 26.06 show no sign of them. Do not raise <code>dustrelayfee</code> or turn on <code>dustdynamic</code> on such a node: anything above the default makes the 330-sat anchors dust, and every anchor force close is refused. Plumb only: <code>rejectfakeoutputs</code> refuses a commitment with three or more HTLC outputs of the same value between 354 and 546 sat; none of the {n(l["ln"])} force closes mined in those blocks carried three or more P2WSH outputs of one value of 546 sat or less. Knots {KNOTS_NAME.split()[-1]} refuses the penalty spend of an LND simple taproot channel: its revocation leaf pushes a 32-byte key and drops it, and Knots counts those 34 bytes as non-standard data. Plumb relays it since plumb9 (knots#487), counting the 34 bytes toward <code>datacarriersize</code>, so one such input fits {DATACARRIER_SIZE["plumb"]}. In those blocks {"no" if not l["ln_taproot_revoke"] else n(l["ln_taproot_revoke"])} mined transaction{"" if l["ln_taproot_revoke"] == 1 else "s"} spent a leaf of that form{", every one of them carrying a JSON token message that Plumb refuses" if l["ln_taproot_revoke"] and l["ln_taproot_revoke_token"] == l["ln_taproot_revoke"] else ""}. The comments above each changed setting say the rest.</p>'''


def settings_section():
    return f'''<h3 id="settings" style="margin-top:22px">Recommended bitcoin.conf</h3>
<p class="small" style="max-width:75ch">The usual ways a filter gets turned off are <code>corepolicy=1</code>, a filter switched off, <code>acceptnonstdtxn=1</code>, or a setting changed in the Knots GUI. The Plumb and Knots files write their filter settings out, so a <code>corepolicy=1</code> set elsewhere can no longer reset them (it still changes settings they do not set, such as the minimum relay fee), and set three values stricter than Knots&#39; defaults: <code>datacarriersize=42</code> (Plumb&#39;s own default since plumb7), <code>datacarriercost=2</code> and <code>bytespersigopstrict=51</code>. The comment above each says what it does, and <a href="#fake-multisig">the last section</a> explains the third. If your <code>bitcoin.conf</code> already sets one of these options, or turns one off with a no line such as <code>nodatacarrier=1</code>, change or remove that line rather than adding a second one. Keep any stricter value you set on purpose, such as <code>datacarrier=0</code>. Knots does not know Plumb&#39;s four options, so use the file for the program you run. A node that a Lightning node sends transactions through takes the <a href="#lightning">Lightning</a> file for its program instead.</p>
<div class="tabs" data-group="conf"><button data-pane="conf-plumb" class=on>Plumb</button><button data-pane="conf-knots">Knots</button><button data-pane="conf-knots-aggressive">Knots, aggressive</button><button data-pane="conf-lightning">Lightning</button></div>{conf_pane("plumb", True)}{conf_pane("knots", False)}{conf_pane("knots-aggressive", False)}{lightning_pane()}
{aggressive_note()}
{lightning_note()}
<p class="small" style="max-width:75ch">The Knots GUI saves these settings to <code>settings.json</code> and <code>bitcoin_rw.conf</code> in the data directory, and both win over <code>bitcoin.conf</code>, as does anything on the command line or in a service file. Remove the matching entries there. After a restart, this shows each value and where it came from:</p>
<pre class="conf">{esc(SETTINGS_CHECK)}</pre>
<p class="small" style="max-width:75ch">When <code>bitcoin.conf</code> sets an option more than once, a line under <code>[main]</code> is the one in use, even though it is listed last. Otherwise the first plain line is, except that a no line such as <code>nodatacarrier=1</code> cancels the lines above it. <code>~/.bitcoin</code> is the default data directory on Linux; use yours if it differs. A Plumb node also logs one <code>Plumb filter</code> line per filter. Plumb&#39;s filter guide measures what <a href="{LOWER_LIMITS}">lower data limits</a> refuse.</p>'''


# What /check/ reads from a node's startup lines. Defaults and the -corepolicy values are Knots 29.4.3's
# (src/policy/policy.h, src/init.cpp); what each filter refuses is from src/policy/policy.cpp. Knots 29.4
# has the same defaults except rejecttokens, which is off, and refuses Runes and OLGA but not Counterparty.
# Older Knots differ in more (no datacarriersize cap before 29.3.knots20260508), so the checker covers
# 29.4 and later and tells anything older to upgrade. plumbFrom: overrides for Plumb from release n on its
# Knots base and for every Plumb on a later base, read from the version (v<base>.plumb<N>); the release
# number starts again at 1 on each base. plumb7 starts datacarriersize at 42 and -corepolicy sets 83.
# rejectdeadbranches and rejectbpub exist in Knots from 29.4.3: "before" with None marks them absent on older
# Knots, plumbFrom keeps rejectdeadbranches on for Plumb on 29.4.2, which shipped it first, and plumbRow
# hides the option row where that Plumb reports it as its own filter instead. The Plumb rows name the
# switches Plumb reports at startup; "until" drops a row for Plumb from that base on, where the rule is
# Knots' own.
# std marks the checks acceptnonstdtxn=1 turns off (IsStandardTx, AreInputsStandard, IsWitnessStandard in
# src/validation.cpp); the data byte limits in ValidateInputs/PreChecks run either way.
# permitlnrevokeleaf is Plumb's switch over knots#487, from plumb9 on, default 1. It is not a filter, so it gets a
# row only when a line sets it ("onlySet"; Knots and older Plumb ignore the unknown option and log no line for it).
# At 1 the 34 bytes LND's revocation leaf drops count toward datacarriersize like OP_RETURN data, so it lets
# nothing past the data limit; at 0 the node refuses the penalty spend of an LND simple taproot channel, as Knots
# 29.4.3 does. That is no filter turned off, so the row says what 0 refuses ("lnd") and does not count as off.
CHECK_SPEC = {
    "since": "v29.4.1",
    "oldest": "v29.4",
    "options": [
        {"name": "rejecttokens", "label": "Runes, Counterparty and OLGA", "kind": "bool", "def": True, "core": False, "good": True, "std": True,
         "before": [["v29.4.1", False]], "oldLabel": "Runes and OLGA; this Knots has no Counterparty check"},
        {"name": "rejectparasites", "label": "CAT-21 mints", "kind": "bool", "def": True, "core": False, "good": True, "std": True},
        {"name": "rejectdeadbranches", "label": "dead conditional branches", "kind": "bool", "def": True, "core": False, "good": True,
         "before": [["v29.4.3", None]], "plumbFrom": {"base": "v29.4.2.knots20260508", "n": 1, "set": {"def": True}},
         "plumbRow": "v29.4.3.knots20260508"},
        {"name": "rejectbpub", "label": "BPUB files in fake multisig keys", "kind": "bool", "def": True, "core": True, "good": True,
         "before": [["v29.4.3", None]]},
        {"name": "datacarrier", "label": "OP_RETURN data at all; 0 refuses every OP_RETURN", "kind": "bool", "def": True, "good": True, "falseIs": "stricter"},
        {"name": "datacarriersize", "label": "most data bytes allowed", "kind": "num", "def": 83, "max": 83, "over": "capped",
         "plumbFrom": {"base": "v29.4.2.knots20260508", "n": 7, "set": {"def": 42, "max": 42, "over": "looser", "core": 83}}},
        {"name": "datacarrierfullcount", "label": "count data outside OP_RETURN too", "kind": "bool", "def": True, "core": False, "good": True},
        {"name": "datacarriercost", "label": "fee weight of each data byte", "kind": "num", "def": 1, "core": 0.25, "min": 1, "under": "looser"},
        {"name": "acceptnonstddatacarrier", "label": "data outside OP_RETURN", "kind": "bool", "def": False, "core": True, "good": False},
        {"name": "permitbaredatacarrier", "label": "an OP_RETURN with no payment beside it", "kind": "bool", "def": False, "core": True, "good": False, "std": True},
        {"name": "permitbarepubkey", "label": "bare public-key outputs", "kind": "bool", "def": False, "core": True, "good": False, "std": True},
        {"name": "permitbaremultisig", "label": "bare multisig outputs", "kind": "bool", "def": False, "core": True, "good": False, "std": True},
        {"name": "maxscriptsize", "label": "largest script and witness, in bytes", "kind": "num", "def": 1650, "core": 4294967295, "max": 1650, "over": "looser", "std": True},
        {"name": "bytespersigopstrict", "label": "least weight for each signature check; 51 refuses most fake multisig", "kind": "num", "def": 20, "core": 0, "min": 20, "under": "looser"},
        {"name": "permitlnrevokeleaf", "label": "the key LND's revocation leaf drops, counted toward datacarriersize", "kind": "bool", "def": True, "good": True,
         "falseIs": "lnd", "onlySet": True},
    ],
    "plumb": [
        {"name": "rejectfakeoutputs", "label": "fake output hashes and keys"},
        {"name": "rejectdeadbranches", "label": "dead conditional branches", "until": "v29.4.3.knots20260508"},
        {"name": "rejectbareenvelopes", "label": "bare data envelopes"},
        {"name": "rejectfakemultisig", "label": "fake multisig keys"},
        {"name": "rejecttokenmessages", "label": "token messages", "std": True},
    ],
}


# Each platform's own names for the filter switches, with the recommended setting: the value in the
# recommended bitcoin.conf, which is Knots 29.4.3's default except the data size and cost and bytes per
# sigop strict. The apps have no switch yet for rejectdeadbranches or rejectbpub, new in 29.4.3 and on by default. Umbrel has no switch for the last (settings.meta.ts); its Advanced tab takes conf lines. Umbrel: Retropex/umbrel-bitcoin libs/settings/settings.meta.ts (Settings, Policy tab).
# StartOS 0.3: Retropex/knots-startos scripts/services/getConfig.ts (Config, Mempool). StartOS 0.4:
# startos/fileModels/bitcoin.conf.ts and actions/config/mempool.ts (Actions, Mempool Settings), where an
# unset switch writes nothing and leaves Knots' default.
PLATFORM_SWITCHES = {
    "umbrel": [("Reject tokens transactions", "on", "the app has started it off; on Knots 29.4 it does not refuse Counterparty"), ("Reject parasitic transactions", "on", ""),
               ("Relay Transactions Containing Arbitrary Data", "on", ""), ("Max Allowed Size of Arbitrary Data in Transactions", "42", "the app starts it at 83"),
               ("Datacarrier cost", "2", "the app starts it at 1"), ("Accept non standard datacarrier", "off", ""), ("Permit Bare Datacarrier", "off", ""),
               ("Permit Bare Pubkey", "off", ""), ("Relay Bare Multisig Transactions", "off", ""), ("Max script size", "1650", "")],
    "startos03": [("Reject Tokens", "on", "the package starts it off; on Knots 29.4 it does not refuse Counterparty"), ("Reject Parasites", "on", ""), ("Datacarrier", "on", ""),
                  ("Datacarrier Size", "42", "the package starts it at 83"), ("Datacarrier cost", "2", "the package starts it at 1"),
                  ("Accept non standard datacarrier", "off", ""),
                  ("Permit bare datacarrier", "off", ""), ("Permit Bare Pubkey", "off", ""), ("Permit Bare Multisig", "off", ""),
                  ("Max Script Size", "1650", ""), ("Bytes Per Sigop Strict", "51", "the package starts it at 20")],
    "startos04": [("Reject Tokens", "unset or on", ""),
                  ("Reject Parasites", "unset or on", ""), ("Relay OP_RETURN Transactions", "unset or on", ""),
                  ("Max OP_RETURN Size", "42", "unset, Knots' default of 83 applies"), ("Datacarrier Cost", "2", "the package starts it at 1"),
                  ("Accept Non-Standard Datacarrier", "unset or off", ""), ("Permit Bare Datacarrier", "unset or off", ""),
                  ("Permit Bare Pubkey", "unset or off", ""), ("Permit Bare Multisig", "unset or off", ""), ("Max Script Size", "unset or 1650", ""),
                  ("Bytes Per Sigop Strict", "51", "unset, Knots' default of 20 applies")],
}


def switch_list(key):
    items = "".join(f'<li><b>{esc(name)}</b>: {esc(value)}' + (f' <span class="flag">({esc(note)})</span>' if note else "") + "</li>"
                    for name, value, note in PLATFORM_SWITCHES[key])
    return f'<ul class="switches">{items}</ul>'


FAKE_MULTISIG_TX = "45becc4a1830e3cda7011ddbbfa819d6a0e3e1c1447d456f5b96f560f4f0fa18"


def fake_multisig_section():
    """Why the files set bytespersigopstrict=51 and keep maxscriptsize at its default, with SIGOP_FACTS."""
    f, g = CONF_FACTS, SIGOP_FACTS
    mss_rest = g["mss_other"] - g["mss_babylon"] - g["mss_liquid"] - g["mss_rsk"]
    return f'''<h2 id="fake-multisig">Fake multisig, and why bytespersigopstrict is 51</h2>
<p style="max-width:75ch">One kind of sewage got past every Knots data limit before 29.4.3: fake multisig, transactions that spend multisig coins whose extra keys carry data, nearly always 1-of-15 and most of them hundreds at once, <a href="/tx/{FAKE_MULTISIG_TX}/">like this one</a>. Each 1-of-15 script lists 15 public keys and needs one signature. One key signs. The other 14 carry data. From block {f["lo"]} to {f["hi"]} cesspool.lol lists {n(g["fake"])} of these. Knots before 29.4.3 relayed every one, even at <code>datacarriersize=42</code>; Knots 29.4.3 refuses all {n(g["fake_knots"])} at its defaults, counting the keys no signature uses (knots#422) and refusing the BPUB file format outright (knots#463). At <code>datacarriersize=42</code> it still relays {n(g["knots42_relays"])} sewage transactions in those blocks.</p>
<p style="max-width:75ch"><b>Why the older data limits missed it.</b> Knots before 29.4.3 counted data in OP_RETURN outputs and data a script pushes only to drop it or skip over it, but it read every key in a multisig script as a key. The fake keys are real points on the curve, so nothing in their format gives them away. What gives them away is that no signature ever uses them.</p>
<p style="max-width:75ch"><b>How Knots 29.4.3 and Plumb refuse it.</b> The unproven-key rule (knots#422, in Knots since 29.4.3, Plumb&#39;s switch <code style="white-space:nowrap">-rejectfakemultisig</code>{source_link("-rejectfakemultisig")}) counts the keys in a multisig script that no signature in the spend uses. It lets 10 unused keys a script go and counts the rest as data. In the transaction above that is 4 keys in each of 411 inputs, 54,252 bytes, far over the data allowance. The BPUB rule (knots#463, <code>-rejectbpub</code>) refuses the file format most of these reveals use whatever the key count. Together they refuse all {n(g["fake_knots"])} of the {n(g["fake"])} in those blocks, on Knots and on Plumb.</p>
<p style="max-width:75ch"><b>How bytespersigopstrict refuses it.</b> Knots counts signature checks in a multisig script: one for each key in segwit, four for each key in plain P2SH, and 20 for any multisig over 16 keys. <code>bytespersigopstrict</code> refuses a transaction with fewer weight units than its value for each check. The default is 20. In segwit each signature adds about 72 weight units, so a 2-of-3 P2WSH spend has about 139 weight units for each check and a segwit 2-of-15 about 55. A plain P2SH 15-of-15 has about 110. The fake 1-of-15 spends carry one signature for 15 keys and come in at about 50. At {g["value"]}, Knots refuses {n(g["fake_value"])} of the {n(g["fake"])} and nothing else from those blocks, and nothing that is not sewage is refused below {g["nearest"]}. The other {n(g["fake"] - g["fake_value"])} need a higher value: {n(g["fake_upto55"])} are refused at 52 to 55, and {n(g["fake_over"])} only above that. A value of 55 would add those {n(g["fake_upto55"])}, but it also refuses a segwit 2-of-16 spend of many coins, which 51 lets through.</p>
<p style="max-width:75ch"><b>What else 51 can refuse.</b> Other multisig with many keys and few signatures can fall below 51 too. On the 29.4.2 release binary it refused a segwit 2-of-17 or 3-of-17 spend of 100 coins and a plain P2SH 2-of-15 spend of one coin to one output. Where the line falls depends on the keys, signatures, coins and outputs. In those blocks 51 refuses nothing but fake 1-of-15.</p>
<p style="max-width:75ch"><b>Why not maxscriptsize.</b> Transaction pages also offer <code>maxscriptsize=589</code> or <code>590</code> for nearly all of these, since their largest witness is 590 or 591 bytes. Size does not tell a fake key from a real one, so that setting refuses every ordinary multisig spend bigger than the value as well. At {g["mss"]}, in the same blocks, it would have refused {n(g["mss_sewage"])} sewage transactions and {n(g["mss_other"])} that are not sewage: {n(g["mss_babylon"])} spends whose script matches Babylon staking, {n(g["mss_liquid"])} that match the Liquid federation, {n(g["mss_rsk"])} that match RSK&#39;s peg, and {n(mss_rest)} others, nearly all multisig, up to 15-of-15 spends of {n(g["largest"])} bytes. The default of 1,650 fits a 15-of-15 spend, and the files keep it.</p>
<p style="max-width:75ch"><b>How far it holds.</b> A sender who drops a key or two gets past 51: a 1-of-14 script comes in at about 51.5. Raising the value soon reaches segwit 2-of-15 spends. The unproven-key rule counts the keys nothing signs for, so it holds until a script is down to 10 of them, and the BPUB rule holds as long as the file keeps its header. 51 is the second check behind both: it depends on neither the key count nor the format, so both files set it.</p>'''


def check_page(tip):
    grep_umbrel = f"{SETTINGS_GREP} {UMBREL_LOG}"
    body = f'''<div class="hero"><div class="kicker">Check your node</div><h1>Is your node filtering?</h1>
<p class="lede">Knots and Plumb refuse spam at their default settings. A setting changed anywhere, or one a node package ships with, can turn a filter off without saying so. Here is how to check.</p></div>
<h2>Recommended settings</h2>
<p style="max-width:75ch">The recommended settings are the ones in the <a href="#settings">bitcoin.conf files below</a>, with the data size at 42 bytes, the data cost at 2 and bytes per sigop strict at 51, all stricter than Knots&#39; defaults of 83, 1 and 20. Plumb&#39;s own default data size is 42 since plumb7. The last one is a second check against <a href="#fake-multisig">fake multisig</a>, which Knots 29.4.3 refuses by default. Here they are on each platform, under the names each one uses. Where a platform starts a switch somewhere else, it says so. A stricter value you chose on purpose, such as turning OP_RETURN data off altogether, is fine to keep.</p>
<h3>Umbrel</h3>
<p style="max-width:75ch">The Bitcoin Knots app in the Umbrel App Store is at version 1.2.13, which runs Knots 29.4. That Knots has no Counterparty check, does not accept block {FORK} or any block after it, and its <b>Reject tokens transactions</b> switch starts off. The app update with Knots 29.4.2, version 1.2.18, has been waiting on the App Store since 21 September (<a href="https://github.com/getumbrel/umbrel-apps/pull/6108">umbrel-apps#6108</a>).</p>
<p style="max-width:75ch">Builds of the app that run Knots 29.4.1 or later, such as the Knots Companion app in the PaulsCode community store that runs Knots 29.4.2, have also started that switch off, although that Knots turns it on. They write <code>rejecttokens=0</code> for the node, so a default node there relays and mines Runes and Counterparty transactions.</p>
<p style="max-width:75ch">Open the app, go to Settings, choose the Policy tab, set these, and save. The app restarts the node with them.</p>
{switch_list("umbrel")}
<p style="max-width:75ch">The app has no switch for <code>bytespersigopstrict</code>. Under Settings, choose the Advanced tab, add the line <code>bytespersigopstrict=51</code> to <b>Custom bitcoin.conf overrides</b>, and save. The app restarts the node with it.</p>
<h3>StartOS 0.3</h3>
<p style="max-width:75ch">Bitcoin Knots for StartOS 0.3 runs Knots 29.4 at its newest, with the same limits as above, and starts Reject Tokens off. Under Config, Mempool:</p>
{switch_list("startos03")}
<h3>StartOS 0.4</h3>
<p style="max-width:75ch">Under Actions &amp; Config, Mempool Settings. A switch left unset writes nothing, so Knots&#39; own default applies, whatever the footnote under it says. Plumb&#39;s StartOS package uses the same screen; its extra filters are on and have no switches there.</p>
{switch_list("startos04")}
<h3>Knots or Plumb, anywhere else</h3>
<p style="max-width:75ch">Set them in <code>bitcoin.conf</code>: <a href="#settings">the recommended files are below</a>, with <a href="#lightning">a Lightning version</a> for a node that a Lightning node sends transactions through.</p>
<h2>Any node: read its startup lines</h2>
<p style="max-width:75ch">Each time it starts, the node writes its version and every setting it was given, with where each one came from, to <code>debug.log</code>. Run this where the node runs, paste the result, and the checker below says which filters are on and what turned any of them off.</p>
<pre class="conf">{esc(SETTINGS_CHECK)}</pre>
<p><button class="copy" data-copy="{esc(SETTINGS_CHECK)}">Copy the command</button></p>
<p class="small" style="max-width:75ch">On Umbrel, over SSH, for the App Store&#39;s Bitcoin Knots app. Other Knots apps keep their data under their own folder in <code>~/umbrel/app-data</code>.</p>
<pre class="conf">{esc(grep_umbrel)}</pre>
<p><button class="copy" data-copy="{esc(grep_umbrel)}">Copy the Umbrel command</button></p>
<div id="checkapp"><form class="checkform"><textarea class="mono" rows="8" spellcheck="false" autocomplete="off" aria-label="Startup lines from debug.log" placeholder="Paste the lines here"></textarea>
<p><button class="btn">Check</button> <span class="small muted">Nothing you paste leaves this page.</span></p></form>
<div class="checkout" aria-live="polite"></div></div>
<script type="application/json" id="checkdata">{json.dumps(CHECK_SPEC, separators=(",", ":"))}</script>
<script src="/static/check.js?v={ASSET_V}" defer></script>
{settings_section()}
{fake_multisig_section()}'''
    return page("/check/", "Check your node", body, nav="check",
                desc="Which spam filters your Knots or Plumb node is using, what turned any of them off, and the settings that put them back.", tip=tip)


def past_page(idx, tip):
    blocks = [idx[h] for h in sorted(idx)]
    pb = [b for b in blocks if past(b)]
    by = {}
    for b in blocks:
        by.setdefault(who(b), {"pool": who(b), "blocks": 0, "past": 0, "text": 0, "txs": 0, "last": None})["blocks"] += 1
    for b in pb:
        p = by[who(b)]
        p["past"] += 1
        p["text"] += named_by_payout(b["h"], b["pool"]) is False
        p["txs"] += past(b)
        p["last"] = b
    pools = sorted([p for p in by.values() if p["past"]], key=lambda p: (-p["past"], -p["txs"]))
    txs = sum(past(b) for b in pb)
    prows = "".join(f'<tr><td>{pool_link(p["pool"])}</td><td class="r num">{n(p["past"])}</td><td class="r num">{n(p["blocks"])}</td>'
                    f'<td class="r num">{pct(p["past"] / p["blocks"])}</td><td class="r num">{n(p["text"])}</td><td class="r num">{n(p["txs"])}</td>'
                    f'<td><a href="/block/{p["last"]["h"]}/">{p["last"]["h"]}</a> <span class="faint">{tm(p["last"]["t"], False)}</span></td></tr>' for p in pools)
    brows = "".join(f'<tr><td><a href="/block/{b["h"]}/">{b["h"]}</a></td><td>{tm(b["t"], False)}</td><td>{pool_link(who(b))}</td>'
                    f'<td>{named_cell(b)}</td><td class="r num">{n(past(b))}</td><td class="wrapc">{past_kinds(b)}</td></tr>' for b in reversed(pb))
    body = f'''<div class="hero"><div class="kicker">Past the defaults</div><h1>Blocks built past the default filters</h1>
<p class="lede">Knots refuses every transaction counted here at its default settings, and Plumb refuses them too. A node at those defaults does not accept them from peers or put them in a block unless its operator overrides the refusal. Each block below holds at least one: it was built on a node without those rules, on one with them turned off or overridden, or by pool software that added transactions the node's mempool did not hold. Refusing is policy, not a consensus rule, so these blocks are valid.</p>
<p class="small muted" style="margin-top:8px">Not counted: dust sweeps and Rootstock PowPeg spends. Knots refuses those at its defaults too, but they <a href="/about/#bar">are not spam</a>: the bytes it counts are padding or a 32-byte commitment.</p>
<p class="small muted" style="margin-top:8px">Pool names come from each block's coinbase text and payout addresses, by the same rules as <a href="https://reorg.watch">reorg.watch</a>. "Named by" says whether the block pays an address on file for the name it carries. Where it does not, only its coinbase text, which anyone can write, ties it to that name.</p></div>
<div class="tiles"><div class="tile"><div class="v">{n(len(pb))} <span class="muted" style="font-size:1rem">/ {n(len(blocks))}</span></div><div class="l">blocks past the defaults</div><div class="s">since the fork</div></div>
<div class="tile"><div class="v">{n(txs)}</div><div class="l">transactions the defaults refuse, mined</div></div>
<div class="tile"><div class="v">{n(len(pools))}</div><div class="l">pools and miners</div></div></div>
<h3>By pool</h3>
<div class="tw"><table><tr><th>Pool</th><th class="r">Blocks past the defaults</th><th class="r">Of its blocks</th><th class="r">Share</th><th class="r">Named by coinbase text only</th><th class="r">Refused txs</th><th>Last one</th></tr>{prows or '<tr><td colspan="7" class="muted">None.</td></tr>'}</table></div>
<h3 style="margin-top:22px">Every block, newest first</h3>
<div class="tw"><table><tr><th>Block</th><th>Time</th><th>Pool</th><th>Named by</th><th class="r">Refused txs</th><th>Kinds</th></tr>{brows or '<tr><td colspan="6" class="muted">None.</td></tr>'}</table></div>
<h3 id="settings" style="margin-top:22px">Keep your node at the defaults</h3>
<p class="small" style="max-width:75ch"><a href="/check/">Check your node</a> has the settings that keep every filter on, the steps for Umbrel and StartOS, and a checker that reads your node&#39;s own startup lines.</p>
<p class="small muted" style="margin-top:22px">{DATUM_NOTE}</p>
{plumb_cta("A Plumb node refuses every transaction on this page.")}'''
    return page("/past-defaults/", "Past the defaults", body, nav="past",
                desc=f"{n(len(pb))} blocks since the fork hold transactions that Knots and Plumb refuse at their defaults.", tip=tip)


def daily_chart(blocks):
    days = collections.OrderedDict()
    for b in blocks:
        d = b["t"] // 86400
        e = days.setdefault(d, [0, 0])
        e[0] += b["w"]
        e[1] += b["sw"]
    if not days:
        return ""
    ks = list(days)
    lo, hi = ks[0], ks[-1]
    span = hi - lo + 1
    mx = max(v[1] / v[0] for v in days.values() if v[0]) or 1
    W, H = 1000, 70
    bw = W / span
    bars = []
    for d, (w, sw) in days.items():
        x = (d - lo) * bw
        h = max(1.5, H * (sw / w) / mx) if sw else 0
        if h:
            bars.append(f'<rect x="{x:.1f}" y="{H - h:.1f}" width="{max(1, bw - 1):.1f}" height="{h:.1f}" fill="var(--sewage)"><title>{datetime.date.fromtimestamp(d * 86400)}: {pct(sw / w, 2)}</title></rect>')
        else:
            bars.append(f'<rect x="{x:.1f}" y="{H - 2}" width="{max(1, bw - 1):.1f}" height="2" fill="var(--water)" opacity=".6"/>')
    return (f'<svg class="spark" viewBox="0 0 {W} {H}" preserveAspectRatio="none">{"".join(bars)}</svg>'
            f'<div class="small faint" style="display:flex;justify-content:space-between"><span>{datetime.date.fromtimestamp(lo * 86400)}</span>'
            f'<span>daily sewage share, peak {pct(mx, 1)}</span><span>{datetime.date.fromtimestamp(hi * 86400)}</span></div>')


def gateway_table(blocks, pool):
    """One row per gateway tag among blocks DATUM gateways built with `pool` upstream: blocks, sewage,
    share of block space, payload, last block. Sorted by sewage transactions, then blocks."""
    by = collections.defaultdict(list)
    for b in blocks:
        by[b.get("dtag") or ""].append(b)
    rows = []
    for tag, bs in sorted(by.items(), key=lambda kv: (-sum(b["sn"] for b in kv[1]), -len(kv[1]), kv[0])):
        w, sw = sum(b["w"] for b in bs), sum(b["sw"] for b in bs)
        last = max(bs, key=lambda b: b["h"])
        rows.append(f'<tr><td>{esc(tag) if tag else "<span class=muted>no tag</span>"}</td><td class="r num">{n(len(bs))}</td>'
                    f'<td class="r num">{n(sum(b["sn"] for b in bs))}</td><td class="r num">{pct(sw / w) if w else "0%"}</td>'
                    f'<td class="r num">{size(sum(b["sd"] for b in bs))}</td><td><a href="/block/{last["h"]}/">{last["h"]}</a> {tm(last["t"], False)}</td></tr>')
    return (f'<h3 style="margin-top:22px">Gateways with {esc(pool)} upstream</h3>'
            f'<p class="small muted" style="margin:0 0 8px;max-width:75ch">One row per tag the gateways wrote. The tag is the second coinbase tag, set by whoever runs the gateway, and anyone can write any tag. '
            f'A gateway&#39;s version is not on the chain. The DATUM protocol sends it to {esc(pool)} in the handshake, where the pool operator can see it next to the connection; nothing here can.</p>'
            f'<div class="tw"><table><tr><th>Gateway tag</th><th class="r">Blocks</th><th class="r">Sewage txs</th><th class="r">Sewage share</th><th class="r">Payload</th><th>Last block</th></tr>{"".join(rows)}</table></div>')


PAYOUT_ROWS = 25  # payout addresses listed on a pool page


def payout_table(blocks, pool, fstate):
    """The addresses these blocks' coinbases paid most, from Your Blocks' state of each block's paying outputs."""
    paid = collections.defaultdict(lambda: [0, 0, 0])  # blocks, sats, last height
    for b in blocks:
        st = fstate.get(str(b["h"]))
        if not st:
            continue
        for a in {a for a, _ in st["o"]}:
            paid[a][0] += 1
            paid[a][2] = max(paid[a][2], b["h"])
        for a, s in st["o"]:
            paid[a][1] += s
    if not paid:
        return ""
    total = sum(p[1] for p in paid.values())
    on_file = pool_addresses().get(pool, set())
    top = sorted(paid.items(), key=lambda kv: (-kv[1][1], kv[0]))[:PAYOUT_ROWS]
    rows = "".join(f'<tr><td><code class="ad">{esc(a)}</code> <a class="small" href="/address/#{esc(a)}">look up</a></td><td class="r num">{n(p[0])}</td>'
                   f'<td class="r num">{btc(p[1], 8)}</td><td class="r num">{pct(p[1] / total)}</td>'
                   f'<td><a href="/block/{p[2]}/">{p[2]}</a></td><td class="small muted">{f"on file for {esc(pool)}" if a in on_file else ""}</td></tr>'
                   for a, p in top)
    nb, na = len(blocks), len(paid)
    whose = "The coinbase of its one block" if nb == 1 else f"The coinbases of its {n(nb)} blocks"
    to = "1 address" if na == 1 else f"{n(na)} addresses"
    listed = ("The address is below, with a look up link to Address Lookup." if na == 1 else
              "Every one is below, each with a look up link to Address Lookup." if na <= PAYOUT_ROWS else
              f"The {PAYOUT_ROWS} paid the most are below, each with a look up link to Address Lookup.")
    return (f'<h3 style="margin-top:22px">Payout addresses</h3><p class="small muted" style="margin-bottom:8px">{whose} since the fork '
            f'paid {btc(total, 8)} BTC to {to}. {listed} A pool that pays its miners in the coinbase shows their addresses here. '
            f'A pool that holds rewards and pays its miners later does not: their addresses never appear in a coinbase.</p>'
            f'<div class="tw"><table><tr><th>Address</th><th class="r">Blocks</th><th class="r">BTC</th><th class="r">Share</th>'
            f'<th>Last block</th><th></th></tr>{rows}</table></div>')


def pool_page(idx, name, tip, fstate):
    everything = window_blocks(idx, None)
    allb = [b for b in everything if who(b) == name]
    dtm = name.startswith(miner.DATUM_PREFIX)
    base = name[len(miner.DATUM_PREFIX):] if dtm else name
    via = [] if dtm else [b for b in everything if b["pool"] == name and who(b) != name]
    via_link = f'<a href="/pool/{miner.slug(miner.DATUM_PREFIX + name)}/">{esc(miner.DATUM_PREFIX + name)}</a>'
    if not allb:
        if not via:
            return None
        body = f'''<div class="hero"><div class="kicker">Inspection report</div><h1>{esc(name)}</h1>
<p class="lede">{plural(len(via), "block")} mined through {esc(name)} since the fork, and a DATUM gateway with {esc(name)} upstream built {"its" if len(via) == 1 else "every"} template.
The gateway&#39;s node chooses the transactions, normally the miner&#39;s own, so {"that block is" if len(via) == 1 else "those blocks are"} counted under {via_link}.</p></div>'''
        return page(f"/pool/{miner.slug(name)}/", name, body, nav="shame", desc=f"Every block mined through {name} was built by a DATUM miner.", tip=tip), None, None, None
    p30 = pool_stats(window_blocks(idx, 30 * 86400)).get(name)
    pall = pool_stats(allb)[name]
    cur = p30 or pall
    L = cur["letter"]
    pb = sorted([b for b in allb if past(b)], key=lambda b: -b["h"])
    tiles = f'''<div class="tiles"><div class="tile"><div class="v g-raw">{pct(cur["share"], 2)}</div><div class="l">of its block space is sewage</div><div class="s">{"last 30 days" if p30 else "since the fork"}</div></div>
<div class="tile"><div class="v">{n(cur["sn"])}</div><div class="l">sewage transactions mined</div><div class="s">in {n(cur["dirty"])} of {n(cur["blocks"])} blocks</div></div>
<div class="tile"><div class="v">{btc(cur["sf"])}</div><div class="l">BTC in fees {"paid" if dtm else "taken"} for them</div><div class="s">{pct(cur["fee_share"])} of {"the fees in these blocks" if dtm else "its fee income"}</div></div>
<div class="tile"><div class="v">{n(cur["pristine"])}</div><div class="l">pristine blocks</div><div class="s">{n(cur["nodata"])} with not one byte of data</div></div>
<div class="tile"><div class="v">{n(len(pb))} <span class="muted" style="font-size:1rem">/ {n(len(allb))}</span></div><div class="l">blocks past the defaults</div><div class="s">since the fork, <a href="/past-defaults/">what this means</a></div></div></div>'''
    worst = sorted([b for b in allb if b["sn"]], key=lambda b: -b["sw"])[:15]
    wrows = "".join(f'<tr><td><a href="/block/{b["h"]}/">{b["h"]}</a></td><td>{tm(b["t"], False)}</td><td>{stamp(b["grade"], True)}</td>'
                    f'<td class="r num">{pct(b["share"])}</td><td class="r num">{n(b["sn"])}</td><td class="r num">{size(b["sd"])}</td></tr>' for b in worst)
    tys = sorted(pall["ty"].items(), key=lambda x: -x[1])
    trows = "".join(f'<tr><td><a href="/guide/#{t}">{esc(info(t)["name"])}</a></td><td class="r num">{n(c)}</td><td>{status_chip(t)}</td></tr>' for t, c in tys)
    recent = sorted(allb, key=lambda b: -b["h"])[:12]
    nb = len(allb)
    of_its = f"{n(len(pb))} of its {n(nb)} blocks" if nb != 1 else "Its one block"
    if pb:
        prows = "".join(f'<tr><td><a href="/block/{b["h"]}/">{b["h"]}</a></td><td>{tm(b["t"], False)}</td><td class="r num">{n(past(b))}</td>'
                        f'<td>{named_cell(b)}</td><td class="wrapc">{past_kinds(b)}</td></tr>' for b in pb)
        tonly = sum(1 for b in pb if named_by_payout(b["h"], b["pool"]) is False)
        if not tonly:
            basis = ""
        elif len(pb) == 1:
            basis = f" That block is named {esc(base)} by its coinbase text alone, which anyone can write."
        elif tonly == len(pb):
            basis = f" {'Both' if tonly == 2 else 'All of them'} are named {esc(base)} by their coinbase text alone, which anyone can write."
        else:
            basis = (f" {n(tonly)} of them {'is' if tonly == 1 else 'are'} named {esc(base)} by coinbase text alone, which anyone can write; "
                     f"the rest pay an address on file for it.")
        holds = ("holds a transaction" if past(pb[0]) == 1 else "holds transactions") if len(pb) == 1 else "hold transactions"
        past_part = (f'<h3 style="margin-top:22px">Past the defaults</h3><p class="small muted" style="margin-bottom:8px">{of_its} since the fork '
                     f'{holds} that Knots and Plumb refuse at their default settings.{basis} '
                     f'<a href="/past-defaults/">Every pool and block</a>. {SETTINGS_LINK}.</p>'
                     f'<div class="tw"><table><tr><th>Block</th><th>Time</th><th class="r">Refused txs</th><th>Named by</th><th>Kinds</th></tr>{prows}</table></div>')
    else:
        none = f"None of its {n(nb)} blocks since the fork holds" if nb != 1 else "Its one block since the fork does not hold"
        past_part = (f'<p class="small muted" style="margin-top:22px">{none} a transaction that Knots and Plumb '
                     f'refuse at their default settings. <a href="/past-defaults/">What this means</a>.</p>')
    plumb_part = pct(cur["psw"] / cur["sw"]) if cur["sw"] else "0%"
    gateways = ""
    own = [b for b in allb if b.get("own")]
    own_line = ""
    if own and not dtm:
        np_, nt = sum(1 for b in own if b["own"] == "port"), sum(1 for b in own if b["own"] == "tag")
        hows = [f"{n(np_)} found on a job its public stratum port handed out" if np_ else "",
                f"{n(nt)} tagged with the pool&#39;s own name" if nt else ""]
        own_line = (f' {plural(len(own), "block")} that a gateway with {esc(name)} upstream built count{"s" if len(own) == 1 else ""} here, '
                    f'because the gateway is {esc(name)}&#39;s own: {", ".join(x for x in hows if x)}.')
    if dtm:
        intro = (f'<p style="margin-top:12px;max-width:70ch">These are the blocks mined through {pool_link(base)} whose templates a DATUM gateway with the pool upstream built. '
                 f'The gateway&#39;s node chooses the transactions, normally the miner&#39;s own; the pool sets who the coinbase pays and its first tag, not what goes in the block. '
                 f'A pool serving a stratum port through its own gateway looks the same from the chain; the blocks known to be the pool&#39;s own count on <a href="/pool/{miner.slug(base)}/">its page</a>.</p>')
        gateways = gateway_table(allb, base)
    elif via:
        intro = (f'<p class="small muted" style="margin-top:12px;max-width:70ch">{plural(len(via), "more block")} mined through {esc(name)} '
                 f'{"was" if len(via) == 1 else "were"} built by DATUM gateways with {esc(name)} upstream, so {"it is" if len(via) == 1 else "they are"} counted under {via_link}.{own_line}</p>')
        gateways = gateway_table(via, name)
    elif own_line:
        intro = f'<p class="small muted" style="margin-top:12px;max-width:70ch">{own_line.strip()}</p>'
    else:
        intro = ""
    if cur["sn"] and dtm:
        operator = f'''<div class="panel" style="margin-top:22px"><h3>To the DATUM miners on {esc(base)}</h3>
<p>Your nodes built these templates. They carried {n(cur["sn"])} sewage transactions {"in the last 30 days" if p30 else "since the fork"}, which paid {btc(cur["sf"])} BTC in fees, {pct(cur["fee_share"])} of the fees in these blocks, to store {size(cur["sd"])} of other people's files and token bookkeeping on every node that will ever run.</p>
<p>A Plumb node behind your gateway would have refused {plumb_part} of that block space.{" The rest gets past Plumb's filters." if cur["psw"] < cur["sw"] else ""}</p></div>'''
    elif cur["sn"]:
        operator = f'''<div class="panel" style="margin-top:22px"><h3>To the operator of {esc(name)}</h3>
<p>Your blocks carried {n(cur["sn"])} sewage transactions {"in the last 30 days" if p30 else "since the fork"}. They paid you {btc(cur["sf"])} BTC, {pct(cur["fee_share"])} of your fee income, to store {size(cur["sd"])} of other people's files and token bookkeeping on every node that will ever run.</p>
<p>A Plumb node building your templates would have refused {plumb_part} of that block space.{" The rest gets past Plumb's filters." if cur["psw"] < cur["sw"] else ""}</p></div>'''
    else:
        operator = f'''<div class="panel" style="margin-top:22px"><h3>Clean record</h3><p>{f"No block a DATUM miner built through {esc(base)} carried" if dtm else f"{esc(name)} has not mined"} a single sewage transaction {"in the last 30 days" if p30 else "since the fork"}. That is what this page is for.</p></div>'''
    body = f'''<div class="bhead"><div class="t"><div class="kicker">Inspection report</div><h1>{esc(name)}</h1>
<div class="meta"><span><b class="num">{n(pall["blocks"])}</b> blocks since the fork</span><span>last block <a href="/block/{recent[0]["h"]}/">{recent[0]["h"]}</a> {tm(recent[0]["t"])}</span></div>
{intro}<p style="margin-top:12px"><button class="copy" data-copy="{esc(name)}: grade {L} at cesspool.lol. {pct(cur["share"], 2)} of its block space is spam. {SITE}/pool/{miner.slug(name)}/">Copy share link</button></p></div>
<div class="stampbox" style="text-align:center"><span class="letter gr-{L}">{L}</span><div class="small muted" style="margin-top:10px">{LETTER_TEXT[L]}</div></div></div>
{tiles}
{gateways}
<h2>Since the fork</h2>{daily_chart(allb)}
<div class="strip">{"".join(cube_link(b) for b in recent)}</div>
<div class="grid2"><div><h3>Worst blocks</h3><div class="tw"><table><tr><th>Block</th><th>Time</th><th>Grade</th><th class="r">Share</th><th class="r">Txs</th><th class="r">Payload</th></tr>{wrows or '<tr><td colspan="6" class="muted">None.</td></tr>'}</table></div></div>
<div><h3>What it mined</h3><div class="tw"><table><tr><th>Kind</th><th class="r">Txs</th><th>Filter</th></tr>{trows or '<tr><td colspan="3" class="muted">Nothing.</td></tr>'}</table></div></div></div>
{payout_table(allb, base, fstate)}{past_part}{operator}{plumb_cta() if cur["sn"] else ""}'''
    desc = f"Grade {L}. {pct(cur['share'], 2)} of {name}'s block space is spam."
    og = f"/og/pool/{miner.slug(name)}.png"
    return page(f"/pool/{miner.slug(name)}/", f"{name}: grade {L}", body, nav="shame", desc=desc, og=og, tip=tip), cur, L, p30 is not None


def status_chip(t):
    i = info(t)
    f = i["filter"]
    if f == PLUMB:
        return f'<span class="chip p">Plumb {esc(i.get("option", ""))}</span>'
    if f == KNOTS:
        return f'<span class="chip k">Knots default</span>'
    if f == NONE:
        return f'<span class="chip n">No filter yet</span>'
    if f == NOT_SPAM:
        return f'<span class="chip ok">Not spam</span>'
    return f'<span class="chip a">Allowed</span>'


def guide_page(idx, tip):
    st = type_stats(idx)
    order = {NONE: 0, PLUMB: 1, KNOTS: 2, ALLOWED: 3, NOT_SPAM: 4}
    keys = sorted(st, key=lambda t: (order.get(info(t)["filter"], 4), -st[t]["sewage"], -st[t]["n"]))
    secs = []
    for t in keys:
        s, i = st[t], info(t)
        prs = " ".join(f'<a class="chip" href="{u}">{esc(l)}</a>' for l, u in i.get("prs", []))
        measured = []
        for k, name in VERDICT_NAMES:
            measured.append(f'<tr><td>{name}</td><td class="r num">{n(s[k])} / {n(s["n"])}</td><td class="r num">{pct(s[k] / s["n"] if s["n"] else 0)}</td></tr>')
        relays = f"<b>{PLUMB_NAME} relays these.</b> " if i["filter"] == NONE else ""
        note = f'<div class="note small">{relays}{prose(i["note"])}</div>' if i.get("note") else ""
        case = f'<p class="small"><b>The case for it.</b> {prose(i["case"])} <a href="/about/#bar">The bar</a>.</p>' if i.get("case") else ""
        secs.append(f'''<section class="panel" id="{t}" style="margin:14px 0"><div class="ftype"><div>
<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><h3 style="margin:0">{esc(i["name"])}</h3>{status_chip(t)}{prs}</div>
<p style="margin-top:10px">{prose(i["what"])}</p><p class="muted">{prose(i["how"])}</p>{note}{case}
<p class="small muted">Seen {plural(s["n"], "time")} in {plural(s["blocks"], "block")}, {"its payload counted where the outputs are spent" if i.get("staged") else size(s["data"]) + " of payload"}. First at <a href="/block/{s["first"]}/">{s["first"]}</a>, latest at <a href="/block/{s["last"]}/">{s["last"]}</a>.</p></div>
<div><div class="small muted" style="margin-bottom:4px">Refused, measured on every instance</div><div class="tw"><table class="meas">{"".join(measured)}</table></div></div></div></section>''')
    body = f'''<div class="hero"><div class="kicker">Field guide</div><h1>Every shape we have caught</h1>
<p class="lede">Each kind of spam on the chain since the fork: what it is, how it hides the bytes, and which filter stops it. The refusal rates come from running each policy's code on every instance, so where the label and the measurement disagree, the measurement wins.</p>
<p class="small"><span class="chip n">No filter yet</span> spam no shipped release refuses &nbsp; <span class="chip p">Plumb</span> refused by a Plumb filter or its {DATACARRIER_SIZE["plumb"]}-byte data limit &nbsp;
<span class="chip k">Knots default</span> refused by stock Knots too &nbsp; <span class="chip a">Allowed</span> gray water inside Plumb&#39;s {DATACARRIER_SIZE["plumb"]}-byte default &nbsp;
<span class="chip ok">Not spam</span> refused by a filter, but every byte it counts is padding, a commitment or a payment</p>
<p class="small muted">A filter counts bytes; it cannot say why they are there. Each entry below is held to <a href="/about/#bar">one bar</a>, and where a protocol has defenders, the entry gives their case.</p></div>
{"".join(secs)}
<div class="panel"><h3>Found a new shape?</h3><p class="muted" style="margin:0">Plumb takes filter PRs directly. The bar: its own option, tests, numbers from the chain, and a false-positive check against real payments. <a href="{PLUMB_REPO}/blob/29.x-plumb/.github/CONTRIBUTING.md">How to submit a filter</a>.</p></div>'''
    return page("/guide/", "Field Guide", body, nav="guide", desc="Every kind of Bitcoin spam since the fork, and which filter stops it.", og="/og/site.png", tip=tip)


def plumb_release(tag):
    """plumb4 for v29.4.2.knots20260508.plumb4; the tag itself when it has no Plumb number."""
    m = re.search(r"\.(plumb\d+)$", tag or "")
    return m.group(1) if m else (tag or "")


# The release in which a filter's source replaced the pull request filters.json names as "previously"; the
# manifest does not record it. plumbnode.org's filter table uses the same words.
PREVIOUSLY_UNTIL = {"plumb#2": "plumb9"}


def filter_sources(x):
    """A filters.json entry's pull requests: the source, the one it replaced ("previously"), and the ones that
    changed it since, with the Plumb release each came in ("amended_by"). The filter's own release is its
    "since", in a column of its own."""
    link = lambda src, url: f'<a href="{esc(url)}">{esc(src)}</a>'
    out = link(x["source"], x["url"])
    if x.get("previously"):
        until = PREVIOUSLY_UNTIL.get(x["previously"])
        out += f' (Plumb&#39;s own {link(x["previously"], x["previously_url"])}{" before " + esc(until) if until else ""})'
    for a in x.get("amended_by", []):
        out += f'; changed by {link(a["source"], a["url"])} in {esc(plumb_release(a.get("since")))}'
    return out


def plumb_page(idx, tip):
    allb = window_blocks(idx, None)
    tot = totals(allb)
    filters = PLUMB_FILTERS
    frows = "".join(f'<tr><td><code>{esc(x["option"])}</code></td><td class="wrapc">{esc(x["summary"])}</td><td>{filter_sources(x)}</td>'
                    f'<td>{esc(plumb_release(x.get("since")))}</td></tr>' for x in filters)
    body = f'''<div class="hero"><div class="kicker">The fix</div><h1>Plumb keeps it out of the block</h1>
<p class="lede">Plumb is Bitcoin Knots plus every spam filter that has been reviewed and tested, on by default, in every release. It changes what a node relays and puts in its block templates. It does not change consensus.</p></div>
<div class="tiles"><div class="tile"><div class="v">{size(tot["sd"])}</div><div class="l">of sewage payload mined since the fork</div></div>
<div class="tile"><div class="v">{pct(tot["psw"] / tot["sw"] if tot["sw"] else 0)}</div><div class="l">of that block space a Plumb node refuses</div></div>
<div class="tile"><div class="v">{pct(tot["ksw"] / tot["sw"] if tot["sw"] else 0)}</div><div class="l">refused by stock Knots</div></div>
<div class="tile"><div class="v">{n(tot["sm"])}</div><div class="l">sewage transaction{"" if tot["sm"] == 1 else "s"} {PLUMB_NAME} misses</div><div class="s">{"a fake multisig reveal" if tot["sm"] == 1 else "fake multisig reveals"} small enough to stay under the limit</div></div></div>
<h2>What it adds to Knots</h2><div class="tw"><table><tr><th>Option</th><th>What it counts as data</th><th>Source</th><th>In Plumb since</th></tr>{frows}</table></div>
<p class="small muted" style="margin-top:8px">It also starts <code>datacarriersize</code> at {DATACARRIER_SIZE["plumb"]} bytes where Knots starts at {DATACARRIER_SIZE["knots"]}, which refuses most swap memos, staking tags and text notes. Everything Knots already refuses stays refused: runestones, Counterparty, inscriptions, CAT-21, bare multisig.</p>
<h2>Run it</h2><div class="grid2"><div class="panel"><h3>A node</h3><p class="muted">Build from the signed tag and replace <code>bitcoind</code>. Same config, same data directory, same RPC. Already on Knots or Plumb? <a href="/check/">Check your node</a>.</p>
<p><a class="btn" href="{PLUMB_RELEASE}">Latest release</a><a class="btn ghost" href="{PLUMB_REPO}">Source</a></p></div>
<div class="panel"><h3>A mining node</h3><p class="muted">If you mine through a DATUM pool, your node builds the template. Point your gateway at a Plumb node and your blocks come out clean.</p>
<p><a class="btn ghost" href="{INSTALLER}">knots-datum-node installer</a></p></div></div>
<p class="small muted" style="margin-top:20px">If your transaction is a payment, it goes through. If it isn't, it doesn't.</p>'''
    return page("/plumb/", "Plumb", body, nav="plumb", desc="Plumb: Bitcoin Knots with every reviewed spam filter on by default.", og="/og/site.png", tip=tip)


def found_page(tip):
    body = f'''<div id="foundapp" data-fork="{FORK}">
<div class="hero"><div class="kicker">Your blocks</div><h1>Find the blocks you mined</h1>
<p class="lede">Type a payout address, a pool name or the text in your coinbase. The page lists every block since the fork that matches, graded, and grades the lot the way the Hall of Shame grades a pool.</p></div>
<form class="txform foundform"><input spellcheck="false" autocomplete="off" placeholder="Payout address, pool name or coinbase tag" aria-label="Payout address, pool name or coinbase tag"><button class="btn">Find</button></form>
<div class="foundout" aria-live="polite"></div>
<div class="prose foundhelp"><h2>What finds what</h2>
<ul><li><b>Pool operators:</b> the pool's name as this site shows it, from Kilombino's pools list. The result holds every block mined through the pool, including the ones that DATUM miners' own gateways built.</li>
<li><b>DATUM miners:</b> the second coinbase tag, which you set as <code>coinbase_tag_secondary</code> in your gateway's config. Anyone can write any tag, and the default, <code>DATUM User</code>, is shared by everyone who left it.</li>
<li><b>Solo miners, and miners paid in the coinbase:</b> your payout address. A pool that pays whoever found the block puts your address in that block. A pool that pays every miner a share in the coinbase puts it in blocks you did not find, and the result says so. A pool that holds rewards and pays later never puts it in a coinbase: look that pool up by name.</li></ul>
<p class="small muted">What you type stays after the # in this page's address, which your browser never sends to the server. A height or a transaction id opens that page instead, and the server logs it like any page. <a href="/privacy/">What the server logs</a>.</p></div>
<noscript><p class="muted">This page needs JavaScript to search.</p></noscript></div>'''
    return page("/found/", "Your blocks", body, nav="found", desc="Find the blocks you mined, by payout address, pool name or coinbase tag, and see how they grade.", tip=tip)


def address_page(tip, counts):
    """counts: [addresses a token was directed to since the fork, how many of those got one in a transaction
    that also spent from them], from address.publish."""
    tok, own = counts
    held = "Nobody controls what other people send to their address, but a token stays where it was sent until the owner moves it."
    if tok:
        held += (f" Of the {n(tok)} addresses since the fork that runestones or Counterparty enhanced sends pointed to, {n(own)} ({pct(own / tok)}) "
                 "were pointed to at least once by a transaction that also spent from that address.")
    names = json.dumps({t: i["name"] for t, i in TYPES.items()}, separators=(",", ":"))
    body = f'''<div id="addrapp" data-fork="{FORK}" data-types="{esc(names)}">
<div class="hero"><div class="kicker">Address lookup</div><h1>Has this address handled sewage?</h1>
<p class="lede">Type a Bitcoin address. The page lists every sewage transaction since the fork that spent from it, paid it, or pointed runes or a Counterparty enhanced send at it.</p></div>
<form class="txform foundform"><input spellcheck="false" autocomplete="off" placeholder="Bitcoin address" aria-label="Bitcoin address"><button class="btn">Look up</button></form>
<div class="addrout" aria-live="polite"></div>
<div class="prose foundhelp"><h2>What each line means</h2>
<ul><li><b>Sent:</b> the transaction spent a coin from this address, so its owner took part in it.</li>
<li><b>Runes sent here:</b> the transaction's runestone sends runes to this address, or this address gets whatever runes are left over. Runes stay on that coin until its owner spends it. Whether any arrived depends on what the inputs held, or for a mint or a new rune on whether it succeeded, which this site does not track.</li>
<li><b>Counterparty receiver:</b> a Counterparty send in the transaction names this address as the receiver. If Counterparty accepted the send, it keeps that balance on the address until the owner sends it on. This site does not check whether it did.</li>
<li><b>Got bitcoin:</b> the transaction paid this address and none of the above applies. This site cannot tell a plain payment from a token in a protocol it does not read, and swap services, exchanges and anyone being paid end up here, so this alone says nothing about the owner.</li></ul>
<p>{esc(held)}</p>
<p class="small muted">The list covers what this site grades as sewage, from block {FORK} on. No match means none of those transactions touched the address, not that the address is clean. Outputs that are themselves the data, such as fake addresses made of data, are left out, and so are pay-to-anchor outputs, which anyone can spend. Tokens are read from runestones and from Counterparty's enhanced sends. A receiver named in any other Counterparty message is not listed as a Counterparty receiver, and payments in other token protocols show as Got bitcoin.</p>
<p class="small muted">What you type stays after the # in this page's address, which your browser never sends to the server. The page downloads two files, one of 256 sewage files and one of 256 coinbase payout files, both picked by the same hash of the address, so the server log holds those file names next to your IP address, not the address. Opening a transaction or a block from the result loads that page, and the server logs it like any other. <a href="/privacy/">What the server logs</a>.</p></div>
<noscript><p class="muted">This page needs JavaScript to search.</p></noscript></div>'''
    return page("/address/", "Address lookup", body, nav="address", desc="Look up a Bitcoin address: every sewage transaction since the fork that spent from it, paid it, or pointed runes or a Counterparty enhanced send at it.", tip=tip)


# How about_page says each protocol in KNOWN is matched; the code is classify.known.
KNOWN_RULES = [
    ("ln-commitment", "spends the funding script <code>OP_2 &lt;key&gt; &lt;key&gt; OP_2 OP_CHECKMULTISIG</code> as its only input, lock time starting 0x20, sequence starting 0x80 (BOLT 3)"),
    ("ln-htlc", "the BOLT 3 HTLC script, starting <code>OP_DUP OP_HASH160 &lt;20 bytes&gt; OP_EQUAL OP_IF OP_CHECKSIG</code>"),
    ("ln-penalty", "the BOLT 3 to_local script spent by its revocation branch"),
    ("ln-to-local", "the BOLT 3 to_local script <code>OP_IF &lt;key&gt; OP_ELSE &lt;delay&gt; OP_CSV OP_DROP &lt;key&gt; OP_ENDIF OP_CHECKSIG</code>"),
    ("ln-to-remote", "<code>&lt;key&gt; OP_CHECKSIGVERIFY 1 OP_CSV</code> (BOLT 3, anchor channels)"),
    ("ln-anchor", "the BOLT 3 anchor script <code>&lt;key&gt; OP_CHECKSIG OP_IFDUP OP_NOTIF 16 OP_CSV OP_ENDIF</code>, signed"),
    ("ln-anchor-sweep", "the anchor script with an empty signature, and nothing but an OP_RETURN of 4 bytes or less"),
    ("two-of-two", "the funding script spent any other way"),
    ("p2a-sweep", "spends <code>OP_1 &lt;4e73&gt;</code> outputs, and nothing but an OP_RETURN of 4 bytes or less"),
    ("dust-sweep", "any other spend with nothing but an OP_RETURN of 4 bytes or less"),
    ("powpeg", "the one 457-byte script every PowPeg spend since the fork reveals: <code>OP_NOTIF</code> 5-of-9 <code>OP_ELSE</code> timelock 3-of-4 <code>OP_ENDIF</code> with its 13 keys"),
    ("powpeg-flyover", "the same, behind <code>&lt;32 bytes&gt; OP_DROP</code>"),
    ("liquid", "<code>&lt;m&gt; keys &lt;n&gt; OP_CHECKMULTISIG OP_IFDUP OP_NOTIF &lt;t&gt; OP_CSV OP_VERIFY &lt;m&gt; keys &lt;n&gt; OP_CHECKMULTISIG OP_ENDIF</code>, first branch signed"),
    ("liquid-emergency", "the same, first branch left empty"),
    ("babylon-covenant", "a tapscript <code>&lt;key&gt; OP_CHECKSIGVERIFY</code>, then 4 or more <code>OP_CHECKSIGADD</code>, ending <code>OP_NUMEQUAL</code>"),
    ("babylon", "an OP_RETURN starting <code>bbn1</code>"),
    ("coredao", "an OP_RETURN starting <code>SAT+</code>"),
    ("stacks", "an OP_RETURN starting <code>X2</code> and a Stacks operation byte"),
    ("bip47", "an 80-byte OP_RETURN: version byte, sign byte 02 or 03, 13 zero bytes at the end"),
    ("commitment", "a single 32-byte OP_RETURN that is not text"),
    ("swap-memo", "an OP_RETURN starting with a THORChain-style memo prefix such as <code>=:</code> or <code>SWAP:</code>"),
]


def about_page(idx, tip):
    st, ks = type_stats(idx), known_stats(idx)
    tn = lambda *ts: sum(st.get(t, {}).get("n", 0) for t in ts)
    krows = "".join(f'<tr><td class="wrapc">{esc(KNOWN[k][0])}<div class="small muted">{rule}</div></td><td class="r num">{n(ks.get(k, {}).get("n", 0))}</td>'
                    f'<td class="r num">{n(ks.get(k, {}).get("gray", 0))}</td><td class="r num">{n(ks.get(k, {}).get("sewage", 0))}</td></tr>' for k, rule in KNOWN_RULES)
    body = f'''<div class="hero prose"><div class="kicker">About</div><h1>How the water gets tested</h1>
<p class="lede">Every block since the fork at height {FORK}, every transaction in it, run through the Knots and Plumb policy checks.</p></div>
<div class="prose">
<h2>Verdicts come from the shipped code</h2>
<p>Each transaction, with the coins it spends, goes through the policy checks of {PLUMB_VERSION}: <code>IsStandardTx</code>, <code>AreInputsStandard</code>, the data-carrier count, <code>IsWitnessStandard</code> and the sigop limits, once with each software's default policy: stock Knots 29.4.3 (Plumb's two own filters off, <code>permitlnrevokeleaf=0</code>, and <code>datacarriersize</code> at Knots' {DATACARRIER_SIZE["knots"]}) and Plumb (<code>datacarriersize</code> at {DATACARRIER_SIZE["plumb"]}, its default since plumb7). For Plumb the engine also runs the policy script check, as the node does, and counts the multisig keys again, crediting only those a signature verified against (knots#470); Knots 29.4.3 has no such recount. Bitcoin Core is not compared: it does not accept block {FORK} or any block after it, so no Core node has these blocks. Nothing here reimplements a filter, so this page and a Plumb node cannot disagree about a transaction.</p>
<p>Each refusal on a block or transaction page names the rule it comes from and the option behind it. Plumb's count is also taken with each of five data-counting rules turned off alone, and once with knots#470's recount from verified keys left out, so the bytes a rule is responsible for come from the code too (the sixth, <code>-rejecttokenmessages</code>, names itself in its reason), and a refusal that rests on Knots' own rules says so. So does one that rests on Plumb's {DATACARRIER_SIZE["plumb"]}-byte limit: the count is taken again with the limit at Knots' {DATACARRIER_SIZE["knots"]}.</p>
<p>Where Knots or Plumb relays a transaction shown here, the engine also tries four settings one at a time and keeps the value that would refuse it: the largest <code>datacarriersize</code>; the largest <code>maxscriptsize</code> of {MIN_USEFUL_SCRIPT_LIMIT} or more, the size of the largest push a script may hold, since a limit below that starts refusing ordinary multisig witnesses (a 3-of-5 P2WSH witness is about 395 bytes); the smallest <code>bytespersigopstrict</code> up to {MAX_USEFUL_SIGOP_STRICT}, since above that it starts refusing segwit 2-of-15 spends (a 2-of-15 P2WSH input has about 55 weight units for each of its 15 signature checks; multisig with many keys and few signatures can be refused lower); and the smallest <code>dustrelayfee</code> up to {feerate_conf(MAX_DUST_RATE)} (100 sat/vB, where a P2TR output under 11,000 sat is already dust; higher rates were not tried). Each value comes from rerunning the same checks with only that setting changed. A setting that refuses the transaction at no value in its range is left out, and a verdict that offers none names the four that were tried.</p>
<p>Not modeled: fee floors, mempool limits, replacement rules and address reuse. Those depend on the mempool at the time, not on what the transaction carries.</p>
<h2>Grades</h2>
<ul><li>{stamp("pristine", True)} no sewage. Every transaction is a payment, or carries at most a small note inside Plumb's default {DATACARRIER_SIZE["plumb"]}-byte allowance, such as a 32-byte commitment. We call those notes gray water: the block page counts them and the grade does not.</li>
<li>{stamp("tainted", True)} sewage under 1% of the block's weight.</li>
<li>{stamp("foul", True)} sewage from 1% to 10%.</li>
<li>{stamp("raw", True)} sewage over 10%.</li></ul>
<p>Sewage is any transaction a Plumb node refuses because it carries data, plus known spam shapes that get past Plumb's filters. Those are marked in red as Plumb misses, so the gaps are on the page. The refusals that pass the bar below are not sewage.</p>
<h2 id="bar">Not everything refused is spam</h2>
<p>A filter counts bytes. It cannot say why they are there, so cesspool holds every refusal to one bar. A byte passes when it is one of two things:</p>
<ul><li><b>Script the spend needs.</b> Keys, signatures, hashes and timelocks that the locking script checks when the coins move, including the keys of a multisig branch the spend did not sign with, because the script still evaluates them.</li>
<li><b>Padding the relay rules force.</b> A transaction under 65 non-witness bytes is refused, so a sweep of one input has to carry a few bytes somewhere.</li></ul>
<p>Anything else is data. Small data inside Plumb's default {DATACARRIER_SIZE["plumb"]}-byte allowance, in a transaction that moves coins, stays gray water: it is not counted against anyone, but it does not pass either. Knots allows {DATACARRIER_SIZE["knots"]}, so a note that counts {DATACARRIER_SIZE["plumb"] + 1} to {DATACARRIER_SIZE["knots"]} bytes is sewage here even though stock Knots relays it. Data written for an indexer, a token ledger or another chain fails, however much bitcoin moves beside it. Bytes a script pushes and then drops without using fail too, whatever their size. The bar says what passes; it does not promise that every byte failing it is caught. For one, neither Knots nor Plumb counts witness items a script drops unread, so a transaction carrying them grades clean.</p>
<p>Two kinds of transaction that Knots or Plumb refuses pass the bar. cesspool lists them as gray water under their own names, and their pages still show every refusal:</p>
<ul><li><a href="/guide/#ln-anchor-sweep">Sweeps</a> ({n(tn("ln-anchor-sweep", "p2a-sweep", "dust-sweep"))} since the fork): Lightning anchors, pay-to-anchor outputs and other dust spent into fees, with nothing but an OP_RETURN of {classify.SWEEP_PAD} bytes or less. Knots refuses a transaction whose only output is an OP_RETURN.</li>
<li><a href="/guide/#powpeg-flyover">Rootstock PowPeg Flyover spends</a> ({n(tn("powpeg-flyover"))}): the federation moving a deposit whose script starts with a 32-byte value that it drops unused. Knots counts that push as data.</li></ul>
<p>Each one is kept narrow. A sweep's OP_RETURN holds at most {classify.SWEEP_PAD} bytes, and a sweep whose inputs carry anything Plumb or cesspool counts stays sewage. A Flyover spend is recognized by the exact script every PowPeg spend since the fork reveals, so only the keys in that script can spend a match: 5 of its 9, or 3 of its 4 emergency keys after a long timelock.</p>
<p>Plumb 6 also refused payments back to the vanity address one of the transaction's inputs spends from, reading the zeros in its hash as data. Plumb 7&#39;s <code>-rejectfakeoutputs</code> counts nothing in an output that pays the script of a coin the transaction spends: the spend proved the key or hash in it is real, and fitting chosen data into one would take at least 2<sup>128</sup> tries. Those payments now grade clean.</p>
<p>One kind stays sewage on purpose. A <a href="/guide/#liquid-emergency">Liquid-layout emergency spend</a> ({n(tn("liquid-emergency"))}) is very likely Liquid's own recovery, but every Liquid deposit tweaks the federation keys, so the layout cannot prove that the keys Plumb counts are real, and anyone could copy it.</p>
<p>Protocols with defenders that do not pass have their case in the <a href="/guide/">field guide</a>, next to the reason they fail: inscriptions, Runes, JSON token messages, Stamps, Counterparty, Omni, BIP47 notifications, swap memos, and the tags Babylon, Core DAO and Stacks add. Babylon and Core DAO move real bitcoin to ordinary scripts, and that part passes; the tag beside it is a record for another chain.</p>
<h2 id="known">How protocols are recognized</h2>
<p>By exact scripts and tags, never by a guess about the content. A transaction that matches one of these and also carries other data, a runestone beside a Lightning close say, is graded on the data, and its page says which protocol matched. The PowPeg is pinned to its federation's exact script; the others are matched by template or tag, so a match says what the script is, not who holds the coins.</p>
<div class="tw"><table><tr><th>Protocol, matched by</th><th class="r">Since the fork</th><th class="r">Gray</th><th class="r">Sewage</th></tr>{krows}</table></div>
<p class="small muted">Gray and Sewage count the matches the site lists under that grade. A match listed as sewage carried something the bar does not pass, such as a runestone beside it, or wrote bytes and paid nobody; the Liquid-layout emergency spend above is the one exception.</p>
<h2>What we never show</h2>
<p>The payload. No images, no text, no file names. A spam transaction gets its type, its size and where the bytes sit. Displaying the contents is the service the spammer paid for.</p>
<h2>Who mined it</h2>
<p>Pools are named from the coinbase tag and payout addresses against Kilombino's pools-v2 list, with the same rules as <a href="https://reorg.watch">reorg.watch</a>. {esc(DATUM_NOTE)} Miners and pools can find their own blocks by payout address, pool name or coinbase tag on <a href="/found/">Your Blocks</a>.</p>
<h2>Data</h2><p>One Plumb node's REST interface, checked every 2 seconds. Blocks replaced in a reorg are reprocessed.
The foul and raw-sewage blocks are also an <a href="/feed.xml">Atom feed</a>.</p>
</div>'''
    return page("/about/", "About", body, nav="about", desc="How cesspool.lol grades blocks.", tip=tip)


def privacy_page(tip):
    body = '''<div class="hero prose"><div class="kicker">Privacy</div><h1>What we keep</h1>
<p class="lede">What this site records when you visit, and how long it keeps it.</p></div>
<div class="prose">
<h2>The server log</h2>
<p>Every request that reaches our server goes into its access log with:</p>
<ul><li>your IP address</li>
<li>the date and time</li>
<li>the address you asked for, including any block height or transaction id in it</li>
<li>the page that linked you here, if your browser sends it</li>
<li>your browser's user agent string</li></ul>
<p>A request that fails can also go into the error log, with your IP address, the address you asked for and the page that linked you there.</p>
<p>The access log is deleted after at most 15 days. The error log is cleared out the same way, but only on days that had errors, so its entries can stay longer. We open the logs only to look into problems with the site. We run no analytics on them, and we do not share or sell them.</p>
<h2>Looking up a transaction, your blocks or an address</h2>
<p>A transaction lookup puts the transaction id in the log next to your IP address. If you would rather not leave that link, look up your own transactions on your own node.</p>
<p>On <a href="/found/">Your Blocks</a>, what you type stays after the # in the page's address, which your browser never sends, unless it is a block height or a transaction id, which opens that page like any other. A pool name or coinbase tag lookup downloads the whole name index, so the log shows only that you opened the page. An address lookup downloads the name index as well, then one of 256 address files, picked by a hash of the address, so the log holds that file's name next to your IP address, not the address. Either way the page then fetches one summary file for each thousand-block range the result falls in, and the log shows those too.</p>
<p>On <a href="/address/">Address Lookup</a>, what you type also stays after the #. The page downloads one of 256 sewage files and one of 256 coinbase payout files, both picked by the same hash of the address, so the log holds those two file names next to your IP address, not the address. Opening a transaction or a block from the result loads that page, and the log shows it like any other.</p>
<h2>Cloudflare</h2>
<p>Requests to cesspool.lol go through Cloudflare first. Cloudflare:</p>
<ul><li>sees your IP address and what you asked for</li>
<li>adds its Web Analytics and bot-detection scripts to every page, but this site's security policy stops both from running in your browser</li>
<li>asks your browser to report requests that fail</li>
<li>can set its own cookies, for example when it checks whether a visitor is a bot</li>
<li>shows us some of what it records in its dashboard, which can include IP addresses</li></ul>
<p><a href="https://www.cloudflare.com/privacypolicy/">Cloudflare's privacy policy</a> covers what it keeps.</p>
<h2>Everything else</h2>
<ul><li>This site sets no cookies, and its own code runs no analytics. It has no accounts or ads.</li>
<li>Every script, font and image comes from cesspool.lol. Nothing loads from other sites.</li>
<li>Some pages check for a new block every 10 seconds while they are on screen, and again when you come back to the tab. Each check is logged like any other request.</li>
<li>What you paste into <a href="/check/">Check Node</a> never leaves your browser.</li>
<li>Your browser keeps two notes for this site: the tab you last picked, and the last block a page reloaded for. Neither is sent to us.</li>
<li>Links to other sites leave this one, and those sites have their own policies. Your browser tells them only that you came from cesspool.lol, not which page.</li></ul>
<h2>Questions</h2>
<p>Open an issue on <a href="https://github.com/jasonsopko/cesspool/issues">the site's GitHub repository</a>, where its source and its nginx configuration also live. If the logging changes, this page changes with it.</p>
<p class="small muted">Updated 21 October 2026.</p>
</div>'''
    return page("/privacy/", "Privacy", body, desc="What cesspool.lol records when you visit, and how long it keeps it.", tip=tip)


def blocks_page(idx, tip):
    hs = sorted(idx, reverse=True)[:288]
    rows = "".join(f'<tr><td><a href="/block/{h}/">{h}</a></td><td>{tm(idx[h]["t"])}</td><td>{pool_link(who(idx[h]))}</td>'
                   f'<td>{stamp(idx[h]["grade"], True)}</td><td class="r num">{pct(idx[h]["share"]) if idx[h]["sn"] else ""}</td>'
                   f'<td class="r num">{n(idx[h]["sn"]) if idx[h]["sn"] else ""}</td><td class="r num">{n(idx[h]["gn"])}</td><td class="r num">{n(idx[h]["ntx"])}</td></tr>' for h in hs)
    body = f'''<div class="hero"><div class="kicker">Blocks</div><h1>The last two days of samples</h1>
<p class="lede">Every block has a page. Use the box at the top to jump to any height since {FORK}, or <a href="/found/">find the blocks you mined</a> by payout address, pool name or coinbase tag.</p></div>
<div class="strip">{"".join(cube_link(idx[h]) for h in hs[:16])}</div>
<div class="tw"><table><tr><th>Block</th><th>Time</th><th>Pool</th><th>Grade</th><th class="r">Sewage</th><th class="r">Sewage txs</th><th class="r">Gray</th><th class="r">Txs</th></tr>{rows}</table></div>'''
    return page("/blocks/", "Blocks", body, nav="blocks", desc="Every Bitcoin block graded for spam.", tip=tip)


def index_page(idx, tip):
    hs = sorted(idx, reverse=True)
    latest = [idx[h] for h in hs[:14]]
    d1 = window_blocks(idx, 86400)
    t1 = totals(d1)
    _, ps7, ranked7, honor7, _ = shame_tables(idx, "7d", 7 * 86400, 10)
    tall = totals(window_blocks(idx, None))
    pod = []
    for i, p in enumerate(ranked7[:3]):
        pod.append(f'''<div class="panel"><div class="place">{["Worst", "Second", "Third"][i]} · 7 days</div><div class="name">{pool_link(p["pool"])}</div>
<div class="big g-raw">{pct(p["share"])}</div><div class="small muted">of its block space went to sewage, {n(p["sn"])} transactions.</div></div>''')
    wk = sorted([b for b in window_blocks(idx, 7 * 86400) if b["sn"]], key=lambda b: -b["sd"])
    dump = ""
    if wk:
        b = wk[0]
        dump = f'''<div class="panel" style="display:flex;gap:20px;align-items:center;flex-wrap:wrap"><a class="cube" href="/block/{b["h"]}/">{cube(b)}</a>
<div style="flex:1;min-width:240px"><div class="kicker">Biggest dump this week</div><h3 style="font-size:1.5rem">Block {b["h"]}, {by_html(b)}</h3>
<p>{pct(b["share"])} of the block is sewage: {n(b["sn"])} transactions, {size(b["sd"])} of payload{", mostly " + esc(info(b["top"][1])["name"].lower()) if b["top"] else ""}.</p>
<p><a class="btn ghost" href="/block/{b["h"]}/">Dissect it</a></p></div>{stamp(b["grade"])}</div>'''
    honor = ", ".join(pool_link(p["pool"]) for p in honor7[:8])
    tb = latest[0]
    tl, _ = GRADE[tb["grade"]]
    tline = (f'{pct(tb["share"])} sewage, {n(tb["sn"])} transaction{"s" if tb["sn"] != 1 else ""}.' if tb["sn"]
             else ("Not one byte of data." if not tb["gn"] else f'No sewage. {n(tb["gn"])} small note{"s" if tb["gn"] != 1 else ""}.'))
    body = f'''<div class="herogrid"><div class="hero"><div class="kicker">Bitcoin water quality, block by block</div>
<h1><span>Clean blocks carry payments.</span> <span>The rest is sewage.</span></h1>
<p class="lede">Every block since the fork, every transaction, tested with the policy code a Plumb node runs. See what got mined, who mined it, and which filter would have kept it out.</p>
<p class="lede"><a href="/tx/">Look up any transaction</a> from height {FORK} on, or <a href="/found/">find the blocks you mined</a>.</p></div>
<a class="sample" href="/block/{tb["h"]}/"><div class="kicker">Latest sample</div><div class="bigcube">{cube(tb)}</div>
<div class="sh">Block {tb["h"]}</div><div class="muted small">{esc(short_who(tb))} · {tm(tb["t"])}</div>
<div style="margin:12px 0 6px">{stamp(tb["grade"])}</div><div class="small muted">{tline}</div></a></div>
<div class="strip">{"".join(cube_link(b) for b in latest[1:])}</div>
<h2 style="margin-top:12px">Last 24 hours</h2>
<div class="tiles"><div class="tile"><div class="v g-raw">{pct(t1["share"], 2)}</div><div class="l">of block space taken by sewage</div></div>
<div class="tile"><div class="v">{n(t1["sn"])}</div><div class="l">sewage transactions mined</div></div>
<div class="tile"><div class="v">{btc(t1["sf"])}</div><div class="l">BTC paid to miners to carry it</div></div>
<div class="tile"><div class="v">{n(t1["clean"])} <span class="muted" style="font-size:1rem">/ {n(t1["blocks"])}</span></div><div class="l">pristine blocks</div><div class="s">{n(t1["nodata"])} with not one byte of data</div></div></div>
<h2>Hall of Shame, this week</h2>
<div class="podium">{"".join(pod) or '<div class="panel">Nobody qualified this week.</div>'}</div>
<p><a href="/shame/">Full rankings, biggest dumps, and the clean water roll &rarr;</a></p>
{dump}
<div class="grid2" style="margin-top:22px"><div class="panel"><div class="kicker">Clean water roll, 7 days</div><p style="margin:0">{honor or "Nobody with ten or more blocks went the whole week without sewage."}</p>
<p class="small muted" style="margin:8px 0 0">Ten or more blocks and not one sewage transaction.</p></div>
<div class="panel"><div class="kicker">Since the fork</div><p style="margin:0"><b>{size(tall["sd"])}</b> of sewage payload in <b>{n(tall["sn"])}</b> transactions.
A Plumb node refuses <b>{pct(tall["psw"] / tall["sw"] if tall["sw"] else 0)}</b> of that block space. Stock Knots refuses {pct(tall["ksw"] / tall["sw"] if tall["sw"] else 0)}.</p>
<p class="small muted" style="margin:8px 0 0"><a href="/guide/">What it is and what stops it &rarr;</a></p></div></div>
{plumb_cta()}'''
    return page("/", "", body, desc="Every Bitcoin block tested for spam: what got mined, who mined it, and which filter would have kept it out.", og="/og/site.png", tip=tip)


def feed(idx):
    """Atom feed of the latest foul and raw-sewage blocks."""
    bad = [idx[h] for h in sorted(idx, reverse=True) if idx[h]["grade"] in ("foul", "raw")][:50]
    def iso(t):
        return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = "".join(f'''<entry><title>Block {b["h"]}, {esc(short_who(b))}: {GRADE[b["grade"]][0].lower()}, {pct(b["share"])} spam</title>
<link href="{SITE}/block/{b["h"]}/"/><id>{SITE}/block/{b["h"]}/</id><updated>{iso(b["t"])}</updated>
<summary>{plural(b["sn"], "spam transaction")}, {size(b["sd"])} of payload, {btc(b["sf"])} BTC in fees{", mined through " if who(b) != b["pool"] else " to "}{esc(b["pool"])}.</summary></entry>''' for b in bad)
    upd = iso(bad[0]["t"]) if bad else iso(time.time())
    return (f'''<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>cesspool.lol: foul and raw-sewage blocks</title>
<link href="{SITE}/"/><link rel="self" href="{SITE}/feed.xml"/><id>{SITE}/feed.xml</id><updated>{upd}</updated>{entries}</feed>
''')


# ---------------------------------------------------------------- share images

def og_svg(kind, **k):
    """1200x630 share card."""
    bg, fg, mut = "#0a1316", "#e4edee", "#8ea4a9"
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">'
            f'<rect width="1200" height="630" fill="{bg}"/>'
            f'<text x="70" y="92" font-family="IBM Plex Mono" font-weight="600" font-size="34" fill="{fg}">cesspool<tspan fill="{mut}">.lol</tspan></text>')
    if kind == "block":
        b = k["b"]
        label, _ = GRADE[b["grade"]]
        color = {"pristine": "#8fe6ff", "tainted": "#b5a165", "foul": "#c19243", "raw": "#b98a3c"}[b["grade"]]
        cube_svg = cube(b).replace('<svg viewBox="0 0 128 132" role="img">', '<svg x="820" y="150" width="300" height="310" viewBox="0 0 128 132">')
        cube_svg = cube_svg.replace("var(--panel2)", "#172a30").replace("var(--panel)", "#122126").replace("var(--line)", "#233a41").replace("var(--faint)", "#5d757b")
        return (head + f'<text x="70" y="210" font-family="IBM Plex Sans" font-weight="650" font-size="84" fill="{fg}">Block {b["h"]}</text>'
                f'<text x="70" y="268" font-family="IBM Plex Sans" font-size="{36 if len(by_text(b)) <= 40 else 28}" fill="{mut}">{esc(by_text(b)[:52])}</text>'
                f'<g transform="rotate(-3 300 380)"><rect x="70" y="330" width="{60 + 34 * len(label)}" height="86" fill="none" stroke="{color}" stroke-width="6"/>'
                f'<text x="100" y="391" font-family="IBM Plex Mono" font-weight="700" font-size="48" letter-spacing="6" fill="{color}">{label.upper()}</text></g>'
                f'<text x="70" y="500" font-family="IBM Plex Sans" font-size="38" fill="{fg}">{esc(pct(b["share"]))} of the block is spam: {n(b["sn"])} transaction{"s" if b["sn"] != 1 else ""}</text>'
                f'<text x="70" y="560" font-family="IBM Plex Sans" font-size="28" fill="{mut}">{size(b["sd"])} of payload. Fees taken: {btc(b["sf"])} BTC.</text>'
                + cube_svg + "</svg>")
    if kind == "pool":
        p, L = k["p"], k["L"]
        color = {"A": "#8fe6ff", "B": "#3aa8d8", "C": "#b5a165", "D": "#c19243", "F": "#e0533b"}[L]
        if p["pool"].startswith(miner.DATUM_PREFIX):
            base = p["pool"][len(miner.DATUM_PREFIX):]
            name = (f'<text x="70" y="180" font-family="IBM Plex Sans" font-weight="650" font-size="46" fill="{mut}">DATUM miners via</text>'
                    f'<text x="70" y="246" font-family="IBM Plex Sans" font-weight="650" font-size="{62 if len(base) <= 15 else 44}" fill="{fg}">{esc(base[:24])}</text>')
        else:
            name = f'<text x="70" y="230" font-family="IBM Plex Sans" font-weight="650" font-size="78" fill="{fg}">{esc(p["pool"][:22])}</text>'
        return (head + name +
                f'<text x="70" y="296" font-family="IBM Plex Sans" font-size="34" fill="{mut}">Spam inspection report, {"last 30 days" if k["recent"] else "since the fork"}</text>'
                f'<text x="70" y="400" font-family="IBM Plex Mono" font-weight="600" font-size="64" fill="{fg}">{esc(pct(p["share"], 2))}</text>'
                f'<text x="70" y="450" font-family="IBM Plex Sans" font-size="32" fill="{mut}">of its block space is spam</text>'
                f'<text x="70" y="540" font-family="IBM Plex Sans" font-size="32" fill="{fg}">{n(p["sn"])} spam transactions, {btc(p["sf"])} BTC in fees {"paid" if p["pool"].startswith(miner.DATUM_PREFIX) else "taken"}</text>'
                f'<g transform="rotate(-4 960 320)"><rect x="840" y="200" width="240" height="240" rx="18" fill="none" stroke="{color}" stroke-width="12"/>'
                f'<text x="960" y="385" text-anchor="middle" font-family="IBM Plex Mono" font-weight="700" font-size="190" fill="{color}">{L}</text></g></svg>')
    if kind == "site":
        cubes = ""
        for i, g in enumerate(["pristine", "tainted", "foul", "raw"]):
            fake = {"grade": g, "sn": 0 if g == "pristine" else 1, "share": {"tainted": .005, "foul": .05, "raw": .3}.get(g, 0)}
            c = cube(fake).replace('<svg viewBox="0 0 128 132" role="img">', f'<svg x="{70 + i * 287}" y="330" width="190" height="196" viewBox="0 0 128 132">')
            c = c.replace("var(--panel2)", "#172a30").replace("var(--panel)", "#122126").replace("var(--line)", "#233a41").replace("var(--faint)", "#5d757b")
            cubes += c
        return (head + f'<text x="70" y="200" font-family="IBM Plex Sans" font-weight="650" font-size="62" fill="{fg}">Clean blocks carry payments.</text>'
                f'<text x="70" y="275" font-family="IBM Plex Sans" font-weight="650" font-size="62" fill="#b98a3c">The rest is sewage.</text>' + cubes +
                f'<text x="70" y="590" font-family="IBM Plex Sans" font-size="28" fill="{mut}">Every Bitcoin block tested for spam, and who mined it.</text></svg>')
    if kind == "shame":
        rows = ""
        for i, p in enumerate(k["ranked"][:5]):
            y = 250 + i * 70
            rows += (f'<text x="70" y="{y}" font-family="IBM Plex Mono" font-size="34" fill="{mut}">{i + 1}</text>'
                     f'<text x="130" y="{y}" font-family="IBM Plex Sans" font-weight="600" font-size="40" fill="{fg}">{esc(p["pool"][:34])}</text>'
                     f'<text x="1130" y="{y}" text-anchor="end" font-family="IBM Plex Mono" font-weight="600" font-size="40" fill="#b98a3c">{esc(pct(p["share"], 2))}</text>')
        return (head + f'<text x="70" y="175" font-family="IBM Plex Sans" font-weight="650" font-size="56" fill="{fg}">Hall of Shame, last 7 days</text>' + rows + "</svg>")
    raise ValueError(kind)


_og_drawn = None


def render_png(svg, path):
    """Draw a share card, unless this same SVG already drew the PNG at path."""
    global _og_drawn
    if _og_drawn is None:
        try:
            with open(OG_DRAWN) as f:
                _og_drawn = json.load(f)
        except (OSError, ValueError):
            _og_drawn = {}
    key = hashlib.sha256(svg.encode()).hexdigest()
    if _og_drawn.get(path) == key and os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    r = subprocess.run(["rsvg-convert", "-w", "1200", "-h", "630", "-o", path + ".tmp"], input=svg.encode(), capture_output=True)
    if r.returncode == 0:
        os.replace(path + ".tmp", path)
        _og_drawn[path] = key


def save_og_drawn():
    if _og_drawn is None:
        return
    with open(OG_DRAWN + ".tmp", "w") as f:
        json.dump(_og_drawn, f)
    os.replace(OG_DRAWN + ".tmp", OG_DRAWN)


# ---------------------------------------------------------------- build

def copy_static(out):
    sdir = os.path.join(out, "static")
    os.makedirs(f"{sdir}/fonts", exist_ok=True)
    for name in ("site.css", "site.js", "check.js", "favicon.svg", "beh-discord.png"):
        shutil.copy2(os.path.join(SRC, "static", name), f"{sdir}/{name}")
    fonts = os.path.expanduser("~/.local/share/fonts/plumb")
    for name in ("IBMPlexSans.ttf", "IBMPlexMono-Regular.ttf", "IBMPlexMono-Medium.ttf"):
        if not os.path.exists(f"{sdir}/fonts/{name}"):
            shutil.copy2(f"{fonts}/{name}", f"{sdir}/fonts/{name}")


def build(out, heights=None, all_blocks=False, og=True, pages=False):
    """Render the summary pages, and block pages for `heights` (or all). `pages` renders every block page
    again without republishing the transaction data, as after a change to the block page alone."""
    # A --watch run can build more than once, so nothing read for the last build carries over.
    for cached in (named_by_payout, pool_addresses, miner.survey_kinds):
        cached.cache_clear()
    global _og_drawn
    _og_drawn = None   # read og.json again, so deleting it counts from the next build
    idx, changed = load_index()
    if not idx:
        return
    hs = sorted(idx)
    tip = idx[hs[-1]]
    copy_static(out)
    todo = set(hs) if all_blocks or pages else set(heights or []) | set(changed)
    if not all_blocks:
        # A run that died partway leaves blocks with no page; the next run that renders catches them up.
        todo |= {h for h in hs if not os.path.exists(f"{out}/block/{h}/index.html")}
    # Transaction data goes up before the pages that link to it.
    unpublished = set()
    if not all_blocks:
        # Records a run wrote but never published, for example one that died partway.
        have = set(os.listdir(f"{out}/d/b")) if os.path.isdir(f"{out}/d/b") else set()
        unpublished = {int(n[:-5]) for n in os.listdir(TXD) if n.endswith(".json") and n + ".gz" not in have}
    publish = set(heights or []) | set(changed) | unpublished
    if all_blocks or len(unpublished) > 500:
        # Every block, or too many to index in memory: rebuild the index through temporary files, then
        # write each file. Index first, as in publish_txd: a missing file gets published again, a missing entry would not.
        rebuild_tx_index(out, hs)
        for h in hs:
            if (all_blocks or h in publish) and os.path.exists(f"{TXD}/{h}.json"):
                with open(f"{TXD}/{h}.json") as f:
                    write_gz(f"{out}/d/b/{h}.json.gz", json.load(f))
    else:
        publish_txd(out, sorted(publish))
    # The Your Blocks indexes and page go up before the block pages and header boxes that lead to them.
    fstate = found.publish(out, idx, pool_addresses(), {k: label for k, label, _ in GRADES},
                           [[below, L, LETTER_TEXT[L]] for below, L in LETTER_STEPS] + [[None, "F", LETTER_TEXT["F"]]])
    write(out, "found/index.html", found_page(tip))
    # neighbors' prev/next links
    pos = {h: i for i, h in enumerate(hs)}
    for h in list(todo):
        if h in pos and pos[h] > 0:
            todo.add(hs[pos[h] - 1])
    for h in sorted(todo):
        if h not in idx:
            continue
        with open(f"{BLOCKS}/{h}.json") as f:
            rec = json.load(f)
        i = pos[h]
        prev_h = hs[i - 1] if i > 0 else None
        next_h = hs[i + 1] if i + 1 < len(hs) else None
        write(out, f"block/{h}/index.html", block_page(rec, idx[h], prev_h, next_h, tip))
        if og and idx[h]["sn"]:
            pth = f"{out}/og/block/{h}.png"
            if not os.path.exists(pth) or h in changed:
                render_png(og_svg("block", b=idx[h]), pth)
    write(out, "index.html", index_page(idx, tip))
    write(out, "blocks/index.html", blocks_page(idx, tip))
    write(out, "shame/index.html", shame_page(idx, tip))
    write(out, "past-defaults/index.html", past_page(idx, tip))
    write(out, "check/index.html", check_page(tip))
    for key, (make, _) in CONF_FILES.items():
        write(out, f"check/{key}.conf", make())
    write(out, "guide/index.html", guide_page(idx, tip))
    write(out, "plumb/index.html", plumb_page(idx, tip))
    write(out, "about/index.html", about_page(idx, tip))
    write(out, "privacy/index.html", privacy_page(tip))
    write(out, "tx/index.html", tx_page(tip))
    write_gz(f"{out}/d/catalog.json.gz", tx_catalog())
    # Payout addresses on file for each name in the index, so a transaction page can tell a block
    # named by its payout from one named by its coinbase text alone, as the block page does.
    addrs = pool_addresses()
    write_gz(f"{out}/d/pools.json.gz", {name: sorted(addrs[name]) for name in sorted({b["pool"] for b in idx.values()}) if addrs.get(name)})
    pools = sorted({who(b) for b in idx.values()} | {b["pool"] for b in idx.values()})
    for name in pools:
        r = pool_page(idx, name, tip, fstate)
        if not r:
            continue
        html_, cur, L, recent = r
        write(out, f"pool/{miner.slug(name)}/index.html", html_)
        if cur is None:
            try:
                os.remove(f"{out}/og/pool/{miner.slug(name)}.png")
            except OSError:
                pass
        if og and cur is not None:
            render_png(og_svg("pool", p=cur, L=L, recent=recent), f"{out}/og/pool/{miner.slug(name)}.png")
    # A name that left the index (a renamed pool) would leave its page behind, frozen at an old tip.
    keep = {miner.slug(name) for name in pools}
    pdir = f"{out}/pool"
    for s in (sorted(os.listdir(pdir)) if keep and os.path.isdir(pdir) else []):
        if s in keep:
            continue
        for path in (f"{pdir}/{s}/index.html", f"{out}/og/pool/{s}.png"):
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.rmdir(f"{pdir}/{s}")
        except OSError:
            pass
    if og:
        render_png(og_svg("site"), f"{out}/og/site.png")
        _, _, ranked, _, _ = shame_tables(idx, "7d", 7 * 86400, 10)
        render_png(og_svg("shame", ranked=ranked), f"{out}/og/shame.png")
    write(out, "feed.xml", feed(idx))
    write(out, "robots.txt", "User-agent: *\nAllow: /\n")
    write(out, "404.html", page("/404", "Not found", '<div class="hero"><h1>Nothing down here</h1><p class="lede">That page does not exist. <a href="/">Back to the surface</a>.</p></div>', tip=tip))
    save_og_drawn()
    # After every page that reloads itself, so a page that sees the new tip reloads into pages that already show it.
    write(out, "tip.json", json.dumps({"h": tip["h"]}) + "\n")
    # After the tip: a block of fake multisig reveals touches nearly every address file, a few seconds the
    # block pages need not wait for, and Address Lookup does not reload itself.
    write(out, "address/index.html", address_page(tip, address.publish(out, idx)))
