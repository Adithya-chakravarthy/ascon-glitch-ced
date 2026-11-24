# Per-bit STA slack vs. bits that flipped on the board (AUC, ranks).
#   python sta_compare.py ../data/sta/v0_state.csv ../data/glitch/*_v0_*.jsonl

import csv, glob, json, sys, math
from collections import Counter
sys.path.insert(0, __import__("os").path.dirname(__file__))
from fault_analysis import load, faults, name

def ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v); i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]: j += 1
        for k in range(i, j + 1): r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r

def spearman(a, b):
    ra, rb = ranks(a), ranks(b); n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    return cov / math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))

def auc(score, label):
    pos = [s for s, l in zip(score, label) if l]; neg = [s for s, l in zip(score, label) if not l]
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return wins / (len(pos) * len(neg))

slack = {int(r["bit_index"]): float(r["datapath_slack_ns"]) for r in csv.DictReader(open(sys.argv[1]))}
cnt = Counter()
for p in [f for a in sys.argv[2:] for f in (sorted(glob.glob(a)) or [a])]:
    _, recs = load(p)
    for r in faults(recs):
        cnt.update(r["flips"])
bits = list(range(320))
s = [slack[i] for i in bits]; c = [cnt[i] for i in bits]
rank_sta = {b: k + 1 for k, b in enumerate(sorted(bits, key=lambda i: slack[i]))}
print(f"bits that ever flipped: {sum(1 for x in c if x)} of 320, total flips {sum(c)}")
print(f"Spearman(slack, flip count) over all 320 bits = {spearman(s, c):+.3f}  (negative = slower bits flip more)")
flipped = [i for i in bits if cnt[i]]
print(f"Spearman within the {len(flipped)} flipped bits = {spearman([slack[i] for i in flipped], [cnt[i] for i in flipped]):+.3f}")
print(f"ROC AUC of 'low slack' for predicting 'ever flipped' = {auc([-x for x in s], [x > 0 for x in c]):.3f}  (0.5 = useless, 1 = perfect)")
for top in (10, 20, 30, 40, 60):
    sel = sorted(bits, key=lambda i: slack[i])[:top]
    print(f"  Vivado's {top:2d} slowest bits contain {sum(1 for i in sel if cnt[i]):2d} of the {len(flipped)} flipped bits, "
          f"{100 * sum(cnt[i] for i in sel) / sum(c):5.1f}% of all flips")
print("\nflipped bits, by count:  bit  flips  Vivado rank  slack")
for i in sorted(flipped, key=lambda i: -cnt[i]):
    print(f"  {name(i):7s} {cnt[i]:5d}   #{rank_sta[i]:3d}   {slack[i]:.3f}")
print("\nVivado's 30 slowest bits that NEVER flipped:", ", ".join(f"{name(i)}(#{rank_sta[i]})" for i in sorted(bits, key=lambda i: slack[i])[:30] if not cnt[i]))
print("\nslack band (ns) | bits | bits that flipped | flips")
edges = [3.4, 3.7, 3.9, 4.0, 4.1, 4.2, 4.4, 4.6, 7.0]
for a, b in zip(edges, edges[1:]):
    sel = [i for i in bits if a <= slack[i] < b]
    print(f"  {a:.1f}-{b:.1f}     | {len(sel):4d} | {sum(1 for i in sel if cnt[i]):4d} | {sum(cnt[i] for i in sel):6d}")
