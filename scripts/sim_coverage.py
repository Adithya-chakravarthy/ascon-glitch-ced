# Coverage of each checker for faults injected at the S-box outputs (stuck-at-0),
# the usual simulation-based evaluation. Writes one row per (fault model, checker).

import argparse
import csv
import json
import os
import random
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from gen_gpar import sbox_layer, ROT

M64 = (1 << 64) - 1


def sig_bits(y, mode):
    y0, y1, y2, y3, y4 = [(y >> i) & 1 for i in range(5)]
    if mode == 1:
        return y0 ^ y1 ^ y2 ^ y3 ^ y4
    if mode == 2:
        return (y0 ^ y2 ^ y4) | ((y1 ^ y3) << 1)
    return (y0 ^ y3) | ((y1 ^ y3 ^ y4) << 1) | ((y2 ^ y4) << 2)


def state_error(e_d):
    out = []
    for w in range(5):
        x = e_d[w]
        r1, r2 = ROT[w]
        rot = lambda v, r: ((v >> r) | (v << (64 - r))) & M64
        out.append(x ^ rot(x, r1) ^ rot(x, r2))
    return out


def bits(words):
    return [64 * w + b for w in range(5) for b in range(64) if (words[w] >> b) & 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20000, help="effective faults per fault model")
    ap.add_argument("--groups-dir", default=os.path.join(ROOT, "data", "groups"))
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "sim_coverage.csv"))
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rnd = random.Random(a.seed)

    detectors = {"sbox1": ("sig", 1), "sbox2": ("sig", 2), "sbox3": ("sig", 3), "dup": ("dup", None)}
    for k in (1, 8, 16, 32, 64):
        detectors[f"par_k{k}"] = ("grp", [[i for i in range(320) if i % k == g] for g in range(k)])
    for name in ("sta_sel24", "sta_sel32"):
        f = os.path.join(a.groups_dir, name + ".json")
        if os.path.exists(f):
            detectors[name] = ("grp", json.load(open(f))["groups"])
    gid = {n: {i: g for g, m in enumerate(v[1]) for i in m} for n, v in detectors.items() if v[0] == "grp"}

    models = {
        "SBU (1 output bit stuck-at-0)": lambda: [rnd.randrange(320)],
        "MBU in one S-box (2-5 bits)": lambda: (lambda c: [64 * w + c for w in rnd.sample(range(5), rnd.randint(2, 5))])(rnd.randrange(64)),
        "MBU anywhere (2-8 bits)": lambda: rnd.sample(range(320), rnd.randint(2, 8)),
    }
    rows = []
    for mname, pick in models.items():
        det = Counter()
        n_eff = 0
        while n_eff < a.n:
            S = [rnd.getrandbits(64) for _ in range(5)]
            r = rnd.randrange(12)
            rc = ((0xF - r) << 4) | r
            d = sbox_layer(S, rc)
            e_d = [0] * 5
            for i in pick():
                w, b = divmod(i, 64)
                if (d[w] >> b) & 1:
                    e_d[w] |= 1 << b
            if not any(e_d):
                continue
            n_eff += 1
            cols = {}
            for w in range(5):
                for b in bits([e_d[w] if v == w else 0 for v in range(5)]):
                    c = b % 64
                    cols[c] = cols.get(c, 0) | (1 << w)
            flips = bits(state_error(e_d))
            for name, (kind, arg) in detectors.items():
                if kind == "sig":
                    hit = any(sig_bits(y, arg) for y in cols.values())
                elif kind == "dup":
                    hit = True
                else:
                    g = gid[name]
                    hit = any(v % 2 for v in Counter(g[i] for i in flips).values())
                det[name] += hit
        for name in detectors:
            rows.append({"fault_model": mname, "detector": name, "faults": n_eff,
                         "coverage_pct": round(100 * det[name] / n_eff, 4)})
            print(f"{mname:32s} {name:10s} coverage {100 * det[name] / n_eff:8.4f} %")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("saved", a.out)


if __name__ == "__main__":
    main()
