# Picks the N slowest state bits from a slack CSV (checked exactly, one group each)
# and spreads the remaining bits over --rest interleaved parity groups.
# With --prev it reports whether the selection changed after a rebuild.

import argparse
import csv
import json
import sys


def load_slack(path):
    s = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            s[int(row["bit_index"])] = float(row["datapath_slack_ns"])
    if len(s) != 320:
        sys.exit(f"{path}: expected 320 state bits, found {len(s)}")
    return s


def name(i):
    return f"x{i // 64}[{i % 64}]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slack", required=True)
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--rest", type=int, default=2)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prev", default=None)
    a = ap.parse_args()
    if not 0 <= a.n <= 120 or not 1 <= a.rest <= 8:
        sys.exit("--n must be 0..120 and --rest 1..8")
    s = load_slack(a.slack)
    order = sorted(s, key=lambda i: (s[i], i))
    sel = order[:a.n]
    rest = sorted(order[a.n:])
    groups = [[i] for i in sel] + [rest[g::a.rest] for g in range(a.rest)]
    with open(a.out, "w") as f:
        json.dump({"k": len(groups), "mode": f"stasel{a.n}r{a.rest}", "source": a.slack, "groups": groups}, f)
    worst_sel = s[sel[-1]] if sel else float("nan")
    first_rest = s[order[a.n]] if a.n < 320 else float("nan")
    print(f"selected the {a.n} slowest bits from {a.slack} (slack {s[order[0]]:.3f} .. {worst_sel:.3f} ns); "
          f"next unselected bit {name(order[a.n])} slack {first_rest:.3f} ns; rest split into {a.rest} groups -> {a.out}")
    if a.prev:
        with open(a.prev) as f:
            pg = json.load(f)["groups"]
        prev_sel = {g[0] for g in pg if len(g) == 1}
        now = set(sel)
        same = now == prev_sel
        gone = sorted(prev_sel - now, key=lambda i: s[i])
        new = sorted(now - prev_sel, key=lambda i: s[i])
        print(f"compared with the bits this build checks exactly ({a.prev}): {len(now & prev_sel)} of {a.n} still among "
              f"the {a.n} slowest")
        if new:
            print("  now slow but NOT checked exactly in this build: "
                  + ", ".join(f"{name(i)} (rank {order.index(i) + 1}, {s[i]:.3f} ns)" for i in new))
        if gone:
            print("  checked exactly but no longer among the slowest: "
                  + ", ".join(f"{name(i)} (rank {order.index(i) + 1})" for i in gone))
        core = set(order[:max(0, a.n - 4)])
        if same:
            print("SAME: this build already checks exactly its own slowest bits -> use it")
        elif core <= prev_sel:
            print(f"GOOD: this build checks all of its own {len(core)} slowest bits exactly "
                  f"(only the tail of the list changed) -> use it")
        else:
            print("CHANGED: another pass is needed")


if __name__ == "__main__":
    main()
