"""Download the LongMemEval corpus into data/.

The cleaned release is used: the original is marked deprecated by its author
and its noisy history sessions were removed precisely because they interfere
with answer correctness. We only ever report within-harness comparisons, so
tracking the maintained release costs nothing and keeps the corpus honest.

Usage:  python scripts/fetch_corpus.py [filename ...]
"""

import pathlib
import sys
import urllib.request

BASE = "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main"
DATA = pathlib.Path("data")

# longmemeval_s_cleaned  full haystack, 30-50 sessions per question -- the
#                        benchmark arm. ~277 MB.
# longmemeval_oracle     evidence sessions only. ~15 MB, and the fast loop for
#                        anything that does not need the distractors.
DEFAULT = ["longmemeval_s_cleaned.json", "longmemeval_oracle.json"]


def fetch(name: str) -> pathlib.Path:
    DATA.mkdir(exist_ok=True)
    out = DATA / name
    if out.exists():
        print(f"{name}: present ({out.stat().st_size / 1e6:.0f} MB)")
        return out
    tmp = out.with_suffix(".part")
    print(f"{name}: downloading...")
    urllib.request.urlretrieve(f"{BASE}/{name}", tmp)
    tmp.replace(out)  # ponytail: rename-on-complete, so a killed run re-fetches
    print(f"{name}: {out.stat().st_size / 1e6:.0f} MB")
    return out


if __name__ == "__main__":
    for arg in sys.argv[1:] or DEFAULT:
        fetch(arg)
