"""Render cesspool.lol as static files from the block records."""
import collections, datetime, html, json, math, os, shutil, subprocess, time

from . import classify, miner
from .types import TYPES, info, KNOTS, PLUMB, NONE, ALLOWED

HOME = os.environ.get("CESSPOOL_HOME", os.path.expanduser("~/.cesspool"))
BLOCKS = f"{HOME}/blocks"
INDEX = f"{HOME}/index.json"
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILTERS = os.environ.get("PLUMB_FILTERS", os.path.expanduser("~/src/plumb/plumb/filters.json"))
SITE = "https://cesspool.lol"
EXPLORER = "https://mempool.guide"
PLUMB_REPO = "https://github.com/plumb-node/plumb"
PLUMB_RELEASE = "https://github.com/plumb-node/plumb/releases/latest"
INSTALLER = "https://github.com/jasonsopko/knots-datum-node"
PLUMB_VERSION = "plumb3 (v29.4.2.knots20260508.plumb3)"
PLUMB_NAME = "Plumb 3"
FORK = 961640

GRADES = [  # key, label, css
    ("pristine", "Pristine", "g-pristine"),
    ("clean", "Clean", "g-clean"),
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

def grade(sn, gn, share):
    if sn == 0:
        return "pristine" if gn == 0 else "clean"
    if share < 0.01:
        return "tainted"
    if share < 0.10:
        return "foul"
    return "raw"


def letter(share, blocks_with):
    """Pool grade over a window: share of block weight that is sewage."""
    if share < 0.0005:
        return "A"
    if share < 0.0025:
        return "B"
    if share < 0.01:
        return "C"
    if share < 0.04:
        return "D"
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
        "share": share, "grade": grade(rec["sewage"]["n"], rec["gray"]["n"], share),
        "ty": ty, "top": top,
    }


def load_index():
    idx = {}
    if os.path.exists(INDEX):
        with open(INDEX) as f:
            idx = {int(k): v for k, v in json.load(f).items()}
    changed = []
    for name in os.listdir(BLOCKS):
        if not name.endswith(".json"):
            continue
        h = int(name[:-5])
        p = f"{BLOCKS}/{name}"
        m = os.path.getmtime(p)
        if h not in idx or idx[h].get("_m") != m:
            with open(p) as f:
                rec = json.load(f)
            s = summarize(rec)
            s["_m"] = m
            idx[h] = s
            changed.append(h)
    for h in [h for h in idx if not os.path.exists(f"{BLOCKS}/{h}.json")]:
        del idx[h]
        changed.append(h)
    if changed:
        tmp = INDEX + ".tmp"
        with open(tmp, "w") as f:
            json.dump(idx, f, separators=(",", ":"))
        os.replace(tmp, INDEX)
    return idx, changed


# ---------------------------------------------------------------- aggregates

def pool_stats(blocks):
    by = {}
    for b in blocks:
        p = by.setdefault(b["pool"], {"pool": b["pool"], "blocks": 0, "dirty": 0, "pristine": 0, "w": 0, "sw": 0,
                                       "sn": 0, "sf": 0, "sd": 0, "sm": 0, "fees": 0, "psw": 0, "worst": None,
                                       "ty": collections.Counter()})
        p["blocks"] += 1
        p["dirty"] += b["sn"] > 0
        p["pristine"] += b["grade"] == "pristine"
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
         "clean": 0, "pristine": 0, "raw": 0, "ntx": 0}
    for b in blocks:
        for k in ("w", "sw", "sn", "sf", "sd", "sm", "fees", "psw", "ksw", "ntx"):
            t[k] += b[k]
        t["clean"] += b["sn"] == 0
        t["pristine"] += b["grade"] == "pristine"
        t["raw"] += b["grade"] == "raw"
    t["share"] = t["sw"] / t["w"] if t["w"] else 0
    return t


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

LIQUID = {"pristine": ("#8fe6ff", "#c8f4ff"), "clean": ("#3aa8d8", "#7cc9ea"), "tainted": ("#6e9a9c", "#97b7b4"),
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
            f'<div class="p">{esc(b["pool"])}</div><div class="g {cls}">{label}'
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
    nav_items = [("blocks", "/blocks/", "Blocks"), ("shame", "/shame/", "Hall of Shame"),
                 ("guide", "/guide/", "Field Guide"), ("plumb", "/plumb/", "Plumb"), ("about", "/about/", "About")]
    navh = "".join(f'<a href="{u}"{" class=\"on\"" if k == nav else ""}>{t}</a>' for k, u, t in nav_items)
    full_title = f"{title} · cesspool.lol" if title else "cesspool.lol · Bitcoin water quality, block by block"
    foot_tip = f'Tip {tip["h"]}, {tm(tip["t"])}. ' if tip else ""
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(desc)}">
<meta property="og:title" content="{esc(title or "cesspool.lol")}"><meta property="og:description" content="{esc(desc)}">
<meta property="og:image" content="{SITE}{og}"><meta property="og:url" content="{SITE}{path}"><meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="{SITE}{og}">
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml"><link rel="alternate" type="application/atom+xml" title="Foul and raw-sewage blocks" href="/feed.xml"><link rel="stylesheet" href="/static/site.css?v={ASSET_V}">
</head><body>
<header class="top"><div class="wrap"><a class="brand" href="/">{LOGO}<span>cesspool<span class="tld">.lol</span></span></a>
<nav class="main">{navh}</nav><span class="spacer"></span>
<form class="jump" role="search"><input inputmode="numeric" placeholder="Block height" aria-label="Go to block height"></form></div></header>
<main class="wrap">{body}</main>
<footer><div class="wrap"><div>Verdicts from the policy code in Plumb {PLUMB_VERSION}, run on every transaction.<br>
{foot_tip}Pool names follow <a href="https://reorg.watch">reorg.watch</a>.</div>
<div>We never display what spam carries.<br><a href="/about/">How this works</a></div></div></footer>
<script src="/static/site.js?v={ASSET_V}" defer></script></body></html>
'''


ASSET_V = "1"


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


VERDICT_NAMES = [("core", "Bitcoin Core 31"), ("knots", "Knots 29.4.2"), ("plumb", PLUMB_NAME)]


def verdict_html(v, missed):
    cells = []
    for k, name in VERDICT_NAMES:
        rs = v[k]
        data_rs = [r for r in rs if classify.DATA_REASONS.match(r)]
        if data_rs:
            res = '<span class="stop">Refuses it</span>'
            why = ", ".join(data_rs)
        elif rs:
            res = '<span class="stop">Refuses it</span>'
            why = ", ".join(rs) + " (not a data rule)"
        else:
            res = '<span class="pass">Relays and mines it</span>'
            why = "no rule matches" if not (k == "plumb" and missed) else "no filter for this shape yet"
        cells.append(f'<div class="{"plumb" if k == "plumb" else ""}"><div class="who">{name}</div>{res}<div class="why">{esc(why)}</div></div>')
    return f'<div class="verd">{"".join(cells)}</div>'


def filter_line(t):
    i = info(t)
    if i.get("option") and i["filter"] in (PLUMB, KNOTS):
        prs = " ".join(f'<a href="{u}">{esc(l)}</a>' for l, u in i.get("prs", []))
        who = "Plumb ships" if i["filter"] == PLUMB else "Knots ships"
        return f'{who} <code>{esc(i["option"])}</code>{" (" + prs + ")" if prs else ""}.'
    if i["filter"] == KNOTS:
        return "Knots counts it as data by default."
    if i["filter"] == NONE and i.get("prs"):
        prs = ", ".join(f'<a href="{u}">{esc(l)}</a>' for l, u in i["prs"])
        return f"No shipped filter yet. In review: {prs}."
    return ""


def dissection(s, pool, rate_ctx):
    t = s["types"][0] if s["types"] else "unknown"
    i = info(t)
    cls = "dis" + (" gray" if s["tier"] == "gray" else "") + (" missed" if s["missed"] else "")
    tier_stamp = (f'<span class="stamp sm g-raw">Sewage</span>' if s["tier"] == "sewage" else '<span class="stamp sm" style="color:var(--gray)">Gray water</span>')
    if s["missed"]:
        tier_stamp += ' <span class="stamp sm" style="color:var(--miss)">Plumb miss</span>'
    vbytes = s["vsize"]
    share = min(1, s["data"] / max(1, s.get("size", s["w"] / 4))) if s["data"] else 0
    feerate = s["fee"] / vbytes if vbytes else 0
    others = "".join(f'<span class="chip">{esc(l)}</span>' for l in s["labels"][1:])
    note = ""
    if s["missed"]:
        note = f'<div class="note"><b>{PLUMB_NAME} relays this.</b> {prose(i.get("note", ""))} {filter_line(t)}</div>'
    return f'''<section class="{cls}" id="tx-{s["txid"][:16]}">
<div class="hd">{tier_stamp}<h3>{esc(i["name"])}</h3><span class="muted small">{esc(s["labels"][0]) if s["labels"] else ""}</span></div>
<div class="txid"><a href="{EXPLORER}/tx/{s["txid"]}">{s["txid"]}</a></div>
<p>{prose(i["what"])} <span class="muted">{prose(i["how"])}</span></p>
{f'<p class="small">{others}</p>' if others else ""}
<div class="anat"><div><h4>Inputs ({n(len(s["ins"]))})</h4><div class="io">{io_html(s["ins"], "in", missed=s["missed"])}</div></div>
<div><h4>Outputs ({n(len(s["outs"]))})</h4><div class="io">{io_html(s["outs"], "out")}</div></div></div>
<div class="small muted">Payload: <b class="num" style="color:var(--text)">{n(s["data"])} bytes</b> of a {n(s.get("size", vbytes))}-byte transaction ({n(vbytes)} vB).
Fee <span class="num">{n(s["fee"])}</span> sat ({feerate:.1f} sat/vB), paid to {esc(pool)}.</div>
<div class="fill" title="Share of the transaction that is payload"><span style="width:{share * 100:.1f}%;background:var(--sewage)"></span><span style="flex:1"></span></div>
{verdict_html(s["v"], s["missed"])}
{f'<p class="small" style="margin:10px 0 0">{filter_line(t)}</p>' if filter_line(t) and not s["missed"] else ""}
{note}
</section>'''


def gray_table(gray):
    if not gray:
        return ""
    rows = []
    for s in gray[:400]:
        t = s["types"][0] if s["types"] else "unknown"
        rows.append(f'<tr id="tx-{s["txid"][:16]}"><td>{esc(info(t)["name"])}</td><td class="mono small"><a href="{EXPLORER}/tx/{s["txid"]}">{s["txid"][:16]}…</a></td>'
                    f'<td class="r num">{n(s["data"])}</td><td class="r num">{n(s["vsize"])}</td><td class="r num">{n(s["fee"])}</td></tr>')
    more = f'<p class="small muted">{n(len(gray) - 400)} more not listed.</p>' if len(gray) > 400 else ""
    return f'''<details class="more"><summary>{n(len(gray))} gray-water transactions: small notes inside the default allowance</summary>
<div class="tw"><table><tr><th>Kind</th><th>Transaction</th><th class="r">Data bytes</th><th class="r">vB</th><th class="r">Fee sat</th></tr>{"".join(rows)}</table></div>{more}</details>'''


def block_page(rec, s, prev_h, next_h, tip):
    label, cls = GRADE[s["grade"]]
    sew = [x for x in rec["spam"] if x["tier"] == "sewage"]
    gray = [x for x in rec["spam"] if x["tier"] == "gray"]
    sew.sort(key=lambda x: (-x["missed"], -x["data"]))
    mapdata = {"map": rec["map"], "spam": [{"id": x["txid"][:16], "n": info(x["types"][0])["name"] if x["types"] else "Data",
                                             "d": x["data"], "m": 1 if x["missed"] else 0} for x in rec["spam"]]}
    pool = rec["pool"]
    total_fees = rec["fees"]
    if s["sn"]:
        verdict = (f'{n(s["sn"])} sewage transaction{"s" if s["sn"] != 1 else ""} took <b>{pct(s["share"])}</b> of this block '
                   f'and paid {esc(pool)} <b>{btc(s["sf"])} BTC</b>, {pct(s["sf"] / total_fees if total_fees else 0)} of its fees.')
        if s["sm"]:
            verdict += (" One of them is" if s["sm"] == 1 else f' {n(s["sm"])} of them are') + f" a shape {PLUMB_NAME} does not catch yet."
        caught = s["ps"]
        if caught:
            verdict += f' A Plumb node refuses {n(caught)} of the {n(s["sn"])}.'
    elif s["grade"] == "clean":
        verdict = f'No sewage. {n(s["gn"])} small note{"s" if s["gn"] != 1 else ""} inside the default 83-byte OP_RETURN allowance.'
    else:
        verdict = "Every transaction in this block is a payment. Not one byte of data."
    nav = (f'<div class="navpn">{f"<a href=/block/{prev_h}/>&larr; {prev_h}</a>" if prev_h else ""}'
           f'{f"<a href=/block/{next_h}/>{next_h} &rarr;</a>" if next_h else ""}</div>')
    share_text = f"Block {rec['h']}, mined by {pool}: {label.lower()}" + (f", {pct(s['share'])} of the block is spam." if s["sn"] else ".")
    body = f'''<div class="bhead"><div class="t"><div class="kicker">Block {n(rec["h"])} · sample report</div>
<h1>Block {rec["h"]}</h1>
<div class="meta"><span>Mined by <b>{pool_link(pool)}</b></span><span>{tm(rec["t"])}</span><span><b class="num">{n(rec["ntx"])}</b> transactions</span>
<span><b class="num">{rec["w"] / 4e6 * 100:.0f}%</b> full</span><span>Fees <b class="num">{btc(total_fees)}</b> BTC</span></div>
<p style="margin-top:14px;max-width:70ch">{verdict}</p>
<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">{nav}<button class="copy" data-copy="{esc(share_text)} {SITE}/block/{rec["h"]}/">Copy share link</button>
<a class="small" href="{EXPLORER}/block/{rec["hash"]}">Open in explorer</a></div></div>
<div class="stampbox">{stamp(s["grade"])}</div></div>
<div class="mapbox"><canvas id="map" aria-label="Map of every transaction in the block, sized by virtual size"></canvas><div class="tip"></div></div>
<div class="legend"><span><i style="background:var(--water);opacity:.55"></i>Payment</span><span><i style="background:var(--gray)"></i>Gray water (small note)</span>
<span><i style="background:var(--sewage)"></i>Sewage</span><span><i style="border:2px solid var(--miss)"></i>Sewage {PLUMB_NAME} misses</span></div>
<script type="application/json" id="mapdata">{json.dumps(mapdata, separators=(",", ":"))}</script>
'''
    if sew:
        body += f'<h2>Dissection</h2><p class="muted">Every sewage transaction in the block, what it hides and where, and what each node policy does with it.</p>'
        body += "".join(dissection(x, pool, None) for x in sew[:200])
        if len(sew) > 200:
            body += f'<p class="muted">{n(len(sew) - 200)} more sewage transactions not shown.</p>'
        if s["ps"]:
            body += plumb_cta(f"A Plumb node refuses {n(s['ps'])} of these {n(s['sn'])}.")
    body += gray_table(gray)
    og = f"/og/block/{rec['h']}.png" if s["sn"] else None
    desc = f"Mined by {pool}. " + (f"{pct(s['share'])} of the block is spam: {n(s['sn'])} transactions." if s["sn"] else f"{label}: no sewage.")
    return page(f"/block/{rec['h']}/", f"Block {rec['h']}: {label}", body, nav="blocks", desc=desc, og=og, tip=tip)


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
    wb = "".join(f'<tr><td><a href="/block/{b["h"]}/">{b["h"]}</a></td><td>{pool_link(b["pool"])}</td><td>{stamp(b["grade"], True)}</td>'
                 f'<td class="r num">{pct(b["share"])}</td><td class="r num">{n(b["sn"])}</td><td class="r num">{size(b["sd"])}</td>'
                 f'<td>{esc(info(b["top"][1])["name"]) if b["top"] else ""}</td></tr>' for b in worst_blocks)
    hon = "".join(f'<tr><td>{pool_link(p["pool"])}</td><td class="r num">{n(p["blocks"])}</td><td class="r num">{n(p["pristine"])}</td></tr>' for p in honor)
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
<div class="grid2" style="margin-top:26px"><div><h3>Biggest dumps</h3><div class="tw"><table><tr><th>Block</th><th>Pool</th><th>Grade</th><th class="r">Share</th><th class="r">Txs</th><th class="r">Payload</th><th>Main kind</th></tr>{wb}</table></div></div>
<div class="honor"><h3>Clean water roll</h3><p class="small muted" style="margin-bottom:8px">Pools with {min_blocks} or more blocks in the window and not one sewage transaction.</p>
<div class="tw"><table><tr><th>Pool</th><th class="r">Blocks</th><th class="r">Pristine</th></tr>{hon or '<tr><td colspan="3" class="muted">Nobody qualified.</td></tr>'}</table></div></div></div>
</div>'''


def shame_page(idx, tip):
    tabs = "".join(f'<button data-pane="w-{k}"{" class=on" if k == "7d" else ""}>{l}</button>' for k, l, _, _ in WINDOWS)
    panes = "".join(shame_pane(idx, k, l, s, m) for k, l, s, m in WINDOWS)
    body = f'''<div class="hero"><div class="kicker">Hall of Shame</div><h1>Who is mining the sewage</h1>
<p class="lede">Pools ranked by how much of their block space went to spam. A pool lands here by choosing what goes into its blocks, and it gets off the list the same way.</p></div>
<div class="tabs" data-group="shame">{tabs}</div>{panes}
<p class="small muted" style="margin-top:22px">Grades: A under 0.05% of block space is sewage, B under 0.25%, C under 1%, D under 4%, F above that.
On DATUM pools such as CONVOY, each miner builds the block template on their own node. There the pool name says where a block came from; the template's owner chose its contents.</p>
{plumb_cta("Every pool on this page can leave it with one binary swap.")}'''
    return page("/shame/", "Hall of Shame", body, nav="shame", desc="Pools ranked by how much of their block space went to spam.", og="/og/shame.png", tip=tip)


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


def pool_page(idx, name, tip):
    allb = [b for b in window_blocks(idx, None) if b["pool"] == name]
    if not allb:
        return None
    p30 = pool_stats(window_blocks(idx, 30 * 86400)).get(name)
    pall = pool_stats(allb)[name]
    cur = p30 or pall
    L = cur["letter"]
    tiles = f'''<div class="tiles"><div class="tile"><div class="v g-raw">{pct(cur["share"], 2)}</div><div class="l">of its block space is sewage</div><div class="s">{"last 30 days" if p30 else "since the fork"}</div></div>
<div class="tile"><div class="v">{n(cur["sn"])}</div><div class="l">sewage transactions mined</div><div class="s">in {n(cur["dirty"])} of {n(cur["blocks"])} blocks</div></div>
<div class="tile"><div class="v">{btc(cur["sf"])}</div><div class="l">BTC in fees taken for them</div><div class="s">{pct(cur["fee_share"])} of its fee income</div></div>
<div class="tile"><div class="v">{n(cur["pristine"])}</div><div class="l">pristine blocks</div><div class="s">not one byte of data</div></div></div>'''
    worst = sorted([b for b in allb if b["sn"]], key=lambda b: -b["sw"])[:15]
    wrows = "".join(f'<tr><td><a href="/block/{b["h"]}/">{b["h"]}</a></td><td>{tm(b["t"], False)}</td><td>{stamp(b["grade"], True)}</td>'
                    f'<td class="r num">{pct(b["share"])}</td><td class="r num">{n(b["sn"])}</td><td class="r num">{size(b["sd"])}</td></tr>' for b in worst)
    tys = sorted(pall["ty"].items(), key=lambda x: -x[1])
    trows = "".join(f'<tr><td><a href="/guide/#{t}">{esc(info(t)["name"])}</a></td><td class="r num">{n(c)}</td><td>{status_chip(t)}</td></tr>' for t, c in tys)
    recent = sorted(allb, key=lambda b: -b["h"])[:12]
    plumb_part = pct(cur["psw"] / cur["sw"]) if cur["sw"] else "0%"
    if cur["sn"]:
        operator = f'''<div class="panel" style="margin-top:22px"><h3>To the operator of {esc(name)}</h3>
<p>Your blocks carried {n(cur["sn"])} sewage transactions {"in the last 30 days" if p30 else "since the fork"}. They paid you {btc(cur["sf"])} BTC, {pct(cur["fee_share"])} of your fee income, to store {size(cur["sd"])} of other people's files and token bookkeeping on every node that will ever run.</p>
<p>A Plumb node building your templates would have refused {plumb_part} of that block space.{" The rest is a shape Plumb does not catch yet; those filters are in review." if cur["psw"] < cur["sw"] else ""}</p></div>'''
    else:
        operator = f'''<div class="panel" style="margin-top:22px"><h3>Clean record</h3><p>{esc(name)} has not mined a single sewage transaction {"in the last 30 days" if p30 else "since the fork"}. That is what this page is for.</p></div>'''
    body = f'''<div class="bhead"><div class="t"><div class="kicker">Inspection report</div><h1>{esc(name)}</h1>
<div class="meta"><span><b class="num">{n(pall["blocks"])}</b> blocks since the fork</span><span>last block <a href="/block/{recent[0]["h"]}/">{recent[0]["h"]}</a> {tm(recent[0]["t"])}</span></div>
<p style="margin-top:12px"><button class="copy" data-copy="{esc(name)}: grade {L} at cesspool.lol. {pct(cur["share"], 2)} of its block space is spam. {SITE}/pool/{miner.slug(name)}/">Copy share link</button></p></div>
<div class="stampbox" style="text-align:center"><span class="letter gr-{L}">{L}</span><div class="small muted" style="margin-top:10px">{LETTER_TEXT[L]}</div></div></div>
{tiles}
<h2>Since the fork</h2>{daily_chart(allb)}
<div class="strip">{"".join(cube_link(b) for b in recent)}</div>
<div class="grid2"><div><h3>Worst blocks</h3><div class="tw"><table><tr><th>Block</th><th>Time</th><th>Grade</th><th class="r">Share</th><th class="r">Txs</th><th class="r">Payload</th></tr>{wrows or '<tr><td colspan="6" class="muted">None.</td></tr>'}</table></div></div>
<div><h3>What it mined</h3><div class="tw"><table><tr><th>Kind</th><th class="r">Txs</th><th>Filter</th></tr>{trows or '<tr><td colspan="3" class="muted">Nothing.</td></tr>'}</table></div></div></div>
{operator}{plumb_cta() if cur["sn"] else ""}'''
    desc = f"Grade {L}. {pct(cur['share'], 2)} of {name}'s block space is spam."
    og = f"/og/pool/{miner.slug(name)}.png"
    return page(f"/pool/{miner.slug(name)}/", f"{name}: grade {L}", body, nav="shame", desc=desc, og=og, tip=tip), cur, L


def status_chip(t):
    i = info(t)
    f = i["filter"]
    if f == PLUMB:
        return f'<span class="chip p">Plumb {esc(i.get("option", ""))}</span>'
    if f == KNOTS:
        return f'<span class="chip k">Knots default</span>'
    if f == NONE:
        return f'<span class="chip n">No filter yet</span>'
    return f'<span class="chip a">Allowed</span>'


def guide_page(idx, tip):
    st = type_stats(idx)
    order = {NONE: 0, PLUMB: 1, KNOTS: 2, ALLOWED: 3}
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
        secs.append(f'''<section class="panel" id="{t}" style="margin:14px 0"><div class="ftype"><div>
<div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><h3 style="margin:0">{esc(i["name"])}</h3>{status_chip(t)}{prs}</div>
<p style="margin-top:10px">{prose(i["what"])}</p><p class="muted">{prose(i["how"])}</p>{note}
<p class="small muted">Seen {plural(s["n"], "time")} in {plural(s["blocks"], "block")}, {size(s["data"])} of payload. First at <a href="/block/{s["first"]}/">{s["first"]}</a>, latest at <a href="/block/{s["last"]}/">{s["last"]}</a>.</p></div>
<div><div class="small muted" style="margin-bottom:4px">Refused, measured on every instance</div><div class="tw"><table class="meas">{"".join(measured)}</table></div></div></div></section>''')
    body = f'''<div class="hero"><div class="kicker">Field guide</div><h1>Every shape we have caught</h1>
<p class="lede">Each kind of spam on the chain since the fork: what it is, how it hides the bytes, and which filter stops it. The refusal rates come from running each policy's code on every instance, so where the label and the measurement disagree, the measurement wins.</p>
<p class="small"><span class="chip n">No filter yet</span> spam no shipped release refuses &nbsp; <span class="chip p">Plumb</span> refused by a Plumb filter &nbsp;
<span class="chip k">Knots default</span> refused by stock Knots too &nbsp; <span class="chip a">Allowed</span> gray water inside the default allowance</p></div>
{"".join(secs)}
<div class="panel"><h3>Found a new shape?</h3><p class="muted" style="margin:0">Plumb takes filter PRs directly. The bar: its own option, tests, numbers from the chain, and a false-positive check against real payments. <a href="{PLUMB_REPO}/blob/29.x-plumb/.github/CONTRIBUTING.md">How to submit a filter</a>.</p></div>'''
    return page("/guide/", "Field Guide", body, nav="guide", desc="Every kind of Bitcoin spam since the fork, and which filter stops it.", og="/og/site.png", tip=tip)


def plumb_page(idx, tip):
    allb = window_blocks(idx, None)
    tot = totals(allb)
    filters = []
    try:
        with open(FILTERS) as f:
            filters = json.load(f)
    except OSError:
        pass
    frows = "".join(f'<tr><td><code>{esc(x["option"])}</code></td><td class="wrapc">{esc(x["summary"])}</td><td><a href="{esc(x["url"])}">{esc(x["source"])}</a></td></tr>' for x in filters)
    body = f'''<div class="hero"><div class="kicker">The fix</div><h1>Plumb keeps it out of the block</h1>
<p class="lede">Plumb is Bitcoin Knots plus every spam filter that has been reviewed and tested, on by default, in every release. It changes what a node relays and puts in its block templates. It does not change consensus.</p></div>
<div class="tiles"><div class="tile"><div class="v">{size(tot["sd"])}</div><div class="l">of sewage payload mined since the fork</div></div>
<div class="tile"><div class="v">{pct(tot["psw"] / tot["sw"] if tot["sw"] else 0)}</div><div class="l">of that block space a Plumb node refuses</div></div>
<div class="tile"><div class="v">{pct(tot["ksw"] / tot["sw"] if tot["sw"] else 0)}</div><div class="l">refused by stock Knots</div></div>
<div class="tile"><div class="v">{n(tot["sm"])}</div><div class="l">sewage transactions {PLUMB_NAME} misses</div><div class="s">fake multisig reveals small enough to stay under the limit</div></div></div>
<h2>What it adds to Knots</h2><div class="tw"><table><tr><th>Option</th><th>What it counts as data</th><th>Source</th></tr>{frows}</table></div>
<p class="small muted" style="margin-top:8px">Everything Knots already refuses stays refused: runestones, Counterparty, inscriptions, CAT-21, bare multisig.</p>
<h2>Run it</h2><div class="grid2"><div class="panel"><h3>A node</h3><p class="muted">Build from the signed tag and replace <code>bitcoind</code>. Same config, same data directory, same RPC.</p>
<p><a class="btn" href="{PLUMB_RELEASE}">Latest release</a><a class="btn ghost" href="{PLUMB_REPO}">Source</a></p></div>
<div class="panel"><h3>A mining node</h3><p class="muted">If you mine through a DATUM pool, your node builds the template. Point your gateway at a Plumb node and your blocks come out clean.</p>
<p><a class="btn ghost" href="{INSTALLER}">knots-datum-node installer</a></p></div></div>
<p class="small muted" style="margin-top:20px">If your transaction is a payment, it goes through. If it isn't, it doesn't.</p>'''
    return page("/plumb/", "Plumb", body, nav="plumb", desc="Plumb: Bitcoin Knots with every reviewed spam filter on by default.", og="/og/site.png", tip=tip)


def about_page(tip):
    body = f'''<div class="hero prose"><div class="kicker">About</div><h1>How the water gets tested</h1>
<p class="lede">Every block since the BLAKE2b fork at height {FORK}, every transaction in it, run through three node policies.</p></div>
<div class="prose">
<h2>Verdicts come from the shipped code</h2>
<p>Each transaction, with the coins it spends, goes through the policy checks of Plumb {PLUMB_VERSION}: <code>IsStandardTx</code>, <code>AreInputsStandard</code>, the data-carrier count and <code>IsWitnessStandard</code>, three times over, each with that software's default policy: Bitcoin Core 31 (Knots' <code>-corepolicy</code>, plus the 2,500 legacy-sigop limit and multiple OP_RETURN outputs that Core has had since 30), stock Knots 29.4.2 (Plumb's filters off), and Plumb. No Core node follows this chain, so the Core column is what its defaults would relay. Core 31 allows OP_RETURN outputs up to 100 kB, but BIP110 caps them at 83 bytes by consensus here, so that difference never shows. Nothing here reimplements a filter, so this page and a Plumb node cannot disagree about a transaction.</p>
<p>Not modeled: fee floors, mempool limits, replacement rules and address reuse. Those depend on the mempool at the time, not on what the transaction carries.</p>
<h2>Grades</h2>
<ul><li>{stamp("pristine", True)} every transaction is a payment. Not one byte of data.</li>
<li>{stamp("clean", True)} no sewage. Small OP_RETURN notes inside the default 83-byte allowance only: bridge tags, swap memos, commitments. We call that gray water and do not count it against anyone.</li>
<li>{stamp("tainted", True)} sewage under 1% of the block's weight.</li>
<li>{stamp("foul", True)} sewage from 1% to 10%.</li>
<li>{stamp("raw", True)} sewage over 10%.</li></ul>
<p>Sewage is any transaction a Plumb node refuses because it carries data, plus the known shapes Plumb does not catch yet. Those are marked in red as Plumb misses, so the gaps are on the page.</p>
<h2>What we never show</h2>
<p>The payload. No images, no text, no file names. A spam transaction gets its type, its size and where the bytes sit. Displaying the contents is the service the spammer paid for.</p>
<h2>Who mined it</h2>
<p>Pools are named from the coinbase tag and payout addresses against Kilombino's pools-v2 list, with the same rules as <a href="https://reorg.watch">reorg.watch</a>. On DATUM pools each miner builds the template on their own node; the pool name says where the block came from.</p>
<h2>Data</h2><p>One Plumb node's REST interface, checked every minute. Blocks replaced in a reorg are reprocessed.
The foul and raw-sewage blocks are also an <a href="/feed.xml">Atom feed</a>.</p>
</div>'''
    return page("/about/", "About", body, nav="about", desc="How cesspool.lol grades blocks.", tip=tip)


def blocks_page(idx, tip):
    hs = sorted(idx, reverse=True)[:288]
    rows = "".join(f'<tr><td><a href="/block/{h}/">{h}</a></td><td>{tm(idx[h]["t"])}</td><td>{pool_link(idx[h]["pool"])}</td>'
                   f'<td>{stamp(idx[h]["grade"], True)}</td><td class="r num">{pct(idx[h]["share"]) if idx[h]["sn"] else ""}</td>'
                   f'<td class="r num">{n(idx[h]["sn"]) if idx[h]["sn"] else ""}</td><td class="r num">{n(idx[h]["gn"])}</td><td class="r num">{n(idx[h]["ntx"])}</td></tr>' for h in hs)
    body = f'''<div class="hero"><div class="kicker">Blocks</div><h1>The last two days of samples</h1>
<p class="lede">Every block has a page. Use the box at the top to jump to any height since {FORK}.</p></div>
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
<div style="flex:1;min-width:240px"><div class="kicker">Biggest dump this week</div><h3 style="font-size:1.5rem">Block {b["h"]}, mined by {pool_link(b["pool"])}</h3>
<p>{pct(b["share"])} of the block is sewage: {n(b["sn"])} transactions, {size(b["sd"])} of payload{", mostly " + esc(info(b["top"][1])["name"].lower()) if b["top"] else ""}.</p>
<p><a class="btn ghost" href="/block/{b["h"]}/">Dissect it</a></p></div>{stamp(b["grade"])}</div>'''
    honor = ", ".join(pool_link(p["pool"]) for p in honor7[:8])
    tb = latest[0]
    tl, _ = GRADE[tb["grade"]]
    tline = (f'{pct(tb["share"])} sewage, {n(tb["sn"])} transaction{"s" if tb["sn"] != 1 else ""}.' if tb["sn"]
             else ("Not one byte of data." if tb["grade"] == "pristine" else f'No sewage. {n(tb["gn"])} small note{"s" if tb["gn"] != 1 else ""}.'))
    body = f'''<div class="herogrid"><div class="hero"><div class="kicker">Bitcoin water quality, block by block</div>
<h1>Clean blocks carry payments. The rest is sewage.</h1>
<p class="lede">Every block since the fork, every transaction, tested with the policy code a Plumb node runs. See what got mined, who mined it, and which filter would have kept it out.</p></div>
<a class="sample" href="/block/{tb["h"]}/"><div class="kicker">Latest sample</div><div class="bigcube">{cube(tb)}</div>
<div class="sh">Block {tb["h"]}</div><div class="muted small">{esc(tb["pool"])} · {tm(tb["t"])}</div>
<div style="margin:12px 0 6px">{stamp(tb["grade"])}</div><div class="small muted">{tline}</div></a></div>
<div class="strip">{"".join(cube_link(b) for b in latest[1:])}</div>
<h2 style="margin-top:12px">Last 24 hours</h2>
<div class="tiles"><div class="tile"><div class="v g-raw">{pct(t1["share"], 2)}</div><div class="l">of block space taken by sewage</div></div>
<div class="tile"><div class="v">{n(t1["sn"])}</div><div class="l">sewage transactions mined</div></div>
<div class="tile"><div class="v">{btc(t1["sf"])}</div><div class="l">BTC paid to miners to carry it</div></div>
<div class="tile"><div class="v">{n(t1["clean"])} <span class="muted" style="font-size:1rem">/ {n(t1["blocks"])}</span></div><div class="l">blocks tested clean</div><div class="s">{n(t1["pristine"])} pristine</div></div></div>
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
    entries = "".join(f'''<entry><title>Block {b["h"]}, {esc(b["pool"])}: {GRADE[b["grade"]][0].lower()}, {pct(b["share"])} spam</title>
<link href="{SITE}/block/{b["h"]}/"/><id>{SITE}/block/{b["h"]}/</id><updated>{iso(b["t"])}</updated>
<summary>{plural(b["sn"], "spam transaction")}, {size(b["sd"])} of payload, {btc(b["sf"])} BTC in fees to {esc(b["pool"])}.</summary></entry>''' for b in bad)
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
        color = {"pristine": "#8fe6ff", "clean": "#3aa8d8", "tainted": "#b5a165", "foul": "#c19243", "raw": "#b98a3c"}[b["grade"]]
        cube_svg = cube(b).replace('<svg viewBox="0 0 128 132" role="img">', '<svg x="820" y="150" width="300" height="310" viewBox="0 0 128 132">')
        cube_svg = cube_svg.replace("var(--panel2)", "#172a30").replace("var(--panel)", "#122126").replace("var(--line)", "#233a41").replace("var(--faint)", "#5d757b")
        return (head + f'<text x="70" y="210" font-family="IBM Plex Sans" font-weight="650" font-size="84" fill="{fg}">Block {b["h"]}</text>'
                f'<text x="70" y="268" font-family="IBM Plex Sans" font-size="36" fill="{mut}">mined by {esc(b["pool"][:28])}</text>'
                f'<g transform="rotate(-3 300 380)"><rect x="70" y="330" width="{60 + 34 * len(label)}" height="86" fill="none" stroke="{color}" stroke-width="6"/>'
                f'<text x="100" y="391" font-family="IBM Plex Mono" font-weight="700" font-size="48" letter-spacing="6" fill="{color}">{label.upper()}</text></g>'
                f'<text x="70" y="500" font-family="IBM Plex Sans" font-size="38" fill="{fg}">{pct(b["share"])} of the block is spam: {n(b["sn"])} transaction{"s" if b["sn"] != 1 else ""}</text>'
                f'<text x="70" y="560" font-family="IBM Plex Sans" font-size="28" fill="{mut}">{size(b["sd"])} of payload. Fees taken: {btc(b["sf"])} BTC.</text>'
                + cube_svg + "</svg>")
    if kind == "pool":
        p, L = k["p"], k["L"]
        color = {"A": "#8fe6ff", "B": "#3aa8d8", "C": "#b5a165", "D": "#c19243", "F": "#e0533b"}[L]
        return (head + f'<text x="70" y="230" font-family="IBM Plex Sans" font-weight="650" font-size="78" fill="{fg}">{esc(p["pool"][:22])}</text>'
                f'<text x="70" y="290" font-family="IBM Plex Sans" font-size="34" fill="{mut}">Spam inspection report, last 30 days</text>'
                f'<text x="70" y="400" font-family="IBM Plex Mono" font-weight="600" font-size="64" fill="{fg}">{pct(p["share"], 2)}</text>'
                f'<text x="70" y="450" font-family="IBM Plex Sans" font-size="32" fill="{mut}">of its block space is spam</text>'
                f'<text x="70" y="540" font-family="IBM Plex Sans" font-size="32" fill="{fg}">{n(p["sn"])} spam transactions, {btc(p["sf"])} BTC in fees taken</text>'
                f'<g transform="rotate(-4 960 320)"><rect x="840" y="200" width="240" height="240" rx="18" fill="none" stroke="{color}" stroke-width="12"/>'
                f'<text x="960" y="385" text-anchor="middle" font-family="IBM Plex Mono" font-weight="700" font-size="190" fill="{color}">{L}</text></g></svg>')
    if kind == "site":
        cubes = ""
        for i, g in enumerate(["pristine", "clean", "tainted", "foul", "raw"]):
            fake = {"grade": g, "sn": 0 if g in ("pristine", "clean") else 1, "share": {"tainted": .005, "foul": .05, "raw": .3}.get(g, 0)}
            c = cube(fake).replace('<svg viewBox="0 0 128 132" role="img">', f'<svg x="{70 + i * 215}" y="330" width="190" height="196" viewBox="0 0 128 132">')
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
                     f'<text x="130" y="{y}" font-family="IBM Plex Sans" font-weight="600" font-size="40" fill="{fg}">{esc(p["pool"][:26])}</text>'
                     f'<text x="1130" y="{y}" text-anchor="end" font-family="IBM Plex Mono" font-weight="600" font-size="40" fill="#b98a3c">{pct(p["share"], 2)}</text>')
        return (head + f'<text x="70" y="175" font-family="IBM Plex Sans" font-weight="650" font-size="56" fill="{fg}">Hall of Shame, last 7 days</text>' + rows + "</svg>")
    raise ValueError(kind)


def render_png(svg, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    r = subprocess.run(["rsvg-convert", "-w", "1200", "-h", "630", "-o", path + ".tmp"], input=svg.encode(), capture_output=True)
    if r.returncode == 0:
        os.replace(path + ".tmp", path)


# ---------------------------------------------------------------- build

def copy_static(out):
    sdir = os.path.join(out, "static")
    os.makedirs(f"{sdir}/fonts", exist_ok=True)
    for name in ("site.css", "site.js", "favicon.svg"):
        shutil.copy2(os.path.join(SRC, "static", name), f"{sdir}/{name}")
    fonts = os.path.expanduser("~/.local/share/fonts/plumb")
    for name in ("IBMPlexSans.ttf", "IBMPlexMono-Regular.ttf", "IBMPlexMono-Medium.ttf"):
        if not os.path.exists(f"{sdir}/fonts/{name}"):
            shutil.copy2(f"{fonts}/{name}", f"{sdir}/fonts/{name}")


def build(out, heights=None, all_blocks=False, og=True):
    """Render the summary pages, and block pages for `heights` (or all)."""
    idx, changed = load_index()
    if not idx:
        return
    hs = sorted(idx)
    tip = idx[hs[-1]]
    copy_static(out)
    todo = set(hs) if all_blocks else set(heights or []) | set(changed)
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
    write(out, "guide/index.html", guide_page(idx, tip))
    write(out, "plumb/index.html", plumb_page(idx, tip))
    write(out, "about/index.html", about_page(tip))
    pools = sorted({b["pool"] for b in idx.values()})
    for name in pools:
        r = pool_page(idx, name, tip)
        if not r:
            continue
        html_, cur, L = r
        write(out, f"pool/{miner.slug(name)}/index.html", html_)
        if og:
            render_png(og_svg("pool", p=cur, L=L), f"{out}/og/pool/{miner.slug(name)}.png")
    if og:
        render_png(og_svg("site"), f"{out}/og/site.png")
        _, _, ranked, _, _ = shame_tables(idx, "7d", 7 * 86400, 10)
        render_png(og_svg("shame", ranked=ranked), f"{out}/og/shame.png")
    write(out, "feed.xml", feed(idx))
    write(out, "robots.txt", "User-agent: *\nAllow: /\n")
    write(out, "404.html", page("/404", "Not found", '<div class="hero"><h1>Nothing down here</h1><p class="lede">That page does not exist. <a href="/">Back to the surface</a>.</p></div>', tip=tip))
