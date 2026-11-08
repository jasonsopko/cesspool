"""The read-only API (/api/v1/), for pools and sites that show a block's grade beside it. Static JSON
under <out>/api/v1/, written from the index on every build; site.py calls publish().

  block/<height>.json  one block
  blocks/<n>.json      the blocks from height n*1000 to n*1000+999 that the index holds, sorted by height:
                       {"api": 1, "from": lowest height, "to": highest, "blocks": [...]}. The backfill.
  tip.json             {"api": 1, "height", "hash", "grader_version"} of the highest block

A file is written only when its content changes, through a temporary file and a rename, so a build after
one new block writes that block's file, its thousand's file and tip.json, and every other file keeps its
mtime. Order: each thousand's block files, then its blocks file, then tip.json, then the files of
thousands and heights that left the index. So tip.json always names a height whose file is there, and a
blocks file lists only heights whose files are there.

Which block files to look at, without reading twenty thousand of them each build: a thousand's block
files are written before its blocks file, and before the first of them the thousand is named in the
pending file in the state directory, which is removed once every blocks file is written. A thousand named
there is one a build died in, so its block files may hold something its blocks file does not: each is
compared against its object. Otherwise, while blocks/<n>.json already holds its new content, its block
files hold theirs, and only one missing from the directory needs writing. When it differs, each block whose
object differs from its old entry, or whose file is missing, is compared against its file and written if
that differs too. A new grader_version changes every object, so every file is written again."""
import json, os, re

API = 1
DIR = "api/v1"
CHUNK = 1000
NAME = re.compile(r"(\d+)\.json(\.tmp)?$")


def dump(obj):
    return json.dumps(obj, separators=(",", ":")) + "\n"


def block(b, labels, version, site):
    """The API object for one index summary, keys in the documented order."""
    h = b["h"]
    return {"height": h, "hash": b["hash"], "time": b["t"], "grade": b["grade"], "grade_label": labels[b["grade"]],
            "sewage_share": round(b["share"], 6), "sewage_tx": b["sn"], "gray_tx": b["gn"], "weight": b["w"],
            "url": f"{site}/block/{h}/", "grader_version": version}


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except (OSError, ValueError):  # missing, or not text: write it again
        return None


def write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def entries(text):
    """{height: its object as dump() writes it} from a blocks file's text; {} when it cannot be read."""
    try:
        return {o["height"]: dump(o) for o in json.loads(text)["blocks"]}
    except (TypeError, ValueError, KeyError):
        return {}


def unsure(pending, ks):
    """The thousands a build died in, from the pending file; every thousand in ks when it cannot be read."""
    try:
        with open(pending) as f:
            return {int(k) for k in json.load(f)}
    except FileNotFoundError:
        return set()
    except (OSError, TypeError, ValueError):
        return set(ks)


def publish(out, idx, labels, version, site, pending):
    """Bring <out>/api/v1 in line with idx. labels: grade key -> label; version: grader_version; site: the
    site's URL, for each block's url; pending: the pending file, outside the web root. Returns the files
    written or removed, in order, relative to out."""
    root = os.path.join(out, DIR)
    bdir, cdir = f"{root}/block", f"{root}/blocks"
    os.makedirs(bdir, exist_ok=True)
    os.makedirs(cdir, exist_ok=True)
    done = []

    def put(rel, text, old=False):
        """Write text unless the file holds it already; old, when given, is what the file holds."""
        path = f"{root}/{rel}"
        if (read(path) if old is False else old) != text:
            write(path, text)
            done.append(f"{DIR}/{rel}")

    def drop(d, names):
        for name in sorted(names):
            try:
                os.remove(f"{root}/{d}/{name}")
            except FileNotFoundError:
                continue  # a leftover temporary file that this build's own write used and renamed
            done.append(f"{DIR}/{d}/{name}")

    have, stale = set(), []
    for name in os.listdir(bdir):
        m = NAME.match(name)
        if m and not m.group(2) and int(m.group(1)) in idx:
            have.add(int(m.group(1)))
        elif m:
            stale.append(name)  # a height that left the index, or a write that died before its rename
    chunks = {}
    for h in sorted(idx):
        chunks.setdefault(h // CHUNK, []).append(block(idx[h], labels, version, site))
    plan = []
    for k, objs in sorted(chunks.items()):
        text = dump({"api": API, "from": objs[0]["height"], "to": objs[-1]["height"], "blocks": objs})
        plan.append((k, objs, text, read(f"{cdir}/{k}.json")))
    died = unsure(pending, chunks)
    changing = {k for k, _, text, old in plan if old != text}
    if changing - died:
        write(pending, dump(sorted(died | changing)))  # before the first block file of a changing thousand
    for k, objs, text, old in plan:
        if k in died:
            todo = objs  # a build died here: compare every block file with its object
        elif old == text:
            todo = [o for o in objs if o["height"] not in have]
        else:
            prev = entries(old)
            todo = [o for o in objs if o["height"] not in have or prev.get(o["height"]) != dump(o)]
        for o in todo:
            put(f"block/{o['height']}.json", dump(o))
        put(f"blocks/{k}.json", text, old)
    if died or changing:
        os.remove(pending)  # every blocks file is written: no thousand is out of step now
    tip = idx[max(idx)]
    put("tip.json", dump({"api": API, "height": tip["h"], "hash": tip["hash"], "grader_version": version}))
    # Removals last: a client that read the old tip.json still finds the height it names. A thousand that
    # left the index (a reorg across a thousand boundary) goes before its blocks, as found.publish drops a
    # d/s chunk, so no blocks file lists a height whose file is gone.
    gone = []
    for name in os.listdir(cdir):
        m = NAME.match(name)
        if m and (m.group(2) or int(m.group(1)) not in chunks):
            gone.append(name)
    drop("blocks", gone)
    drop("block", stale)
    return done
