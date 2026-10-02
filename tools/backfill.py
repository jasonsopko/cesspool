#!/usr/bin/env python3
"""Process every cached block into a record: backfill.py FROM TO."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cesspool import process

lo, hi = int(sys.argv[1]), int(sys.argv[2])
step = 100
for a in range(lo, hi + 1, step):
    hs = [h for h in range(a, min(a + step, hi + 1)) if not os.path.exists(f"{process.BLOCKS}/{h}.json")]
    if hs:
        process.process_range(hs)
    print(a, flush=True)
