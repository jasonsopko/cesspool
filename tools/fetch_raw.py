#!/usr/bin/env python3
"""Cache REST blocks (with prevouts) as gzipped JSON: fetch_raw.py FROM TO [DIR]."""
import gzip, json, os, sys, urllib.request

REST = "http://127.0.0.1:8332/rest"


def get(path, timeout=60):
    with urllib.request.urlopen(f"{REST}/{path}", timeout=timeout) as r:
        return r.read()


def main():
    lo, hi = int(sys.argv[1]), int(sys.argv[2])
    out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.environ.get("CESSPOOL_HOME", os.path.expanduser("~/.cesspool")), "raw")
    os.makedirs(out, exist_ok=True)
    for h in range(lo, hi + 1):
        path = f"{out}/{h}.json.gz"
        if os.path.exists(path):
            continue
        bh = json.loads(get(f"blockhashbyheight/{h}.json"))["blockhash"]
        raw = get(f"block/{bh}.json")
        tmp = path + ".tmp"
        with gzip.open(tmp, "wb", compresslevel=6) as f:
            f.write(raw)
        os.replace(tmp, path)
        if h % 500 == 0:
            print(h, flush=True)


if __name__ == "__main__":
    main()
