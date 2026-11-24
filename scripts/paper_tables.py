# Numbers for Table I and Fig. 2 of the paper, computed from the raw campaign logs.
#
#   python paper_tables.py            # prints the table rows and the per-length points

import argparse
import csv
import glob
import json
import math
import os
import re
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STEP_NS = 1.0 / (56 * 0.6)
NAME_RE = re.compile(r"^(v\d\w*?)_v(\d)_(\d{8}_\d{6})\.jsonl$")

TABLE = ["v2_rpar", "v1_sbox", "v5_sbox2", "v6_crc3", "v7_crc3r", "v3_i32", "v4_dup",
         "v3_i8c", "v3_i16c", "v3_i32c", "v3_i64c"]
FIG2 = ["v1_sbox", "v6_crc3", "v7_crc3r", "v3_i32", "v3_i32c"]


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def load(path):
    with open(path) as f:
        recs = [json.loads(line) for line in f if line.strip()]
    meta = next((r for r in recs if r.get("meta")), {})
    return meta, [r for r in recs if not r.get("meta") and not r.get("lost")]


def faulty(r):
    return bool(r["flips"] or not r["tag_ok"] or r["timeout"])


def code_fn(variant, groups_file):
    # True if an ideal checker with this code would flag the flipped bits
    if variant == 4:
        return lambda flips: True
    if variant == 2:
        return lambda flips: len(flips) % 2 == 1
    if variant == 3 and os.path.exists(groups_file):
        with open(groups_file) as f:
            groups = json.load(f)["groups"]
        gid = {i: g for g, members in enumerate(groups) for i in members}
        return lambda flips: any(c % 2 for c in Counter(gid[i] for i in flips).values())
    return None     # S-box checks: faults in the state register are outside their code


def build_stats(label, path, args):
    meta, runs = load(path)
    var = meta["variant"]
    sf = [r for r in runs if r["flips"]]
    intact = [r for r in runs if not faulty(r)]
    code = code_fn(var, os.path.join(args.groups, f"{label}_groups.json"))
    s = {"variant": var, "faults": len(sf), "missed": sum(1 for r in sf if not r["alarm"]),
         "intact": len(intact), "intact_alarms": sum(1 for r in intact if r["alarm"])}
    if code:
        s["code"] = sum(1 for r in sf if code(r["flips"]))
        s["masked"] = sum(1 for r in sf if code(r["flips"]) and not r["alarm"])
    elif var in (1, 5, 6, 7):
        s["code"] = 0
    per = defaultdict(lambda: [0, 0, 0, 0])      # faults, missed, intact runs, alarms on them
    for r in runs:
        p = per[r["phase"]]
        if r["flips"]:
            p[0] += 1
            p[1] += not r["alarm"]
        elif not faulty(r):
            p[2] += 1
            p[3] += bool(r["alarm"])
    s["per_phase"] = dict(per)
    clean = sorted(glob.glob(os.path.join(args.glitch, f"clean_{label}_v*_*.jsonl")))
    if clean:
        _, cr = load(clean[-1])
        s["clean"] = (sum(r["alarm"] for r in cr), len(cr))
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glitch", default=os.path.join(ROOT, "data", "glitch"))
    ap.add_argument("--groups", default=os.path.join(ROOT, "data", "groups"))
    ap.add_argument("--cost", default=os.path.join(ROOT, "data", "cost.csv"))
    ap.add_argument("--min-runs", type=int, default=20, help="points in Fig. 2 need this many runs")
    args = ap.parse_args()

    files = defaultdict(list)
    for f in sorted(glob.glob(os.path.join(args.glitch, "v*_v*_*.jsonl"))):
        m = NAME_RE.match(os.path.basename(f))
        if m:
            files[m.group(1)].append(f)
    stats = {lab: build_stats(lab, fl[-1], args) for lab, fl in files.items()}

    cost = {}
    if os.path.exists(args.cost):
        with open(args.cost) as f:
            rows = {r["label"]: r for r in csv.DictReader(f)}
        base = rows.get("v0")
        for lab, r in rows.items():
            if base:
                cost[lab] = (int(r["impl_core_luts"]) - int(base["impl_core_luts"]),
                             int(r["impl_core_ffs"]) - int(base["impl_core_ffs"]))

    print("Table I")
    print(f"{'build':10s} {'+LUT':>5s} {'+FF':>4s} {'code%':>6s} {'faults':>6s} "
          f"{'missed % (95% CI)':>24s} {'masked':>6s} {'intact%':>7s} {'clean':>7s}")
    for lab in TABLE:
        s = stats.get(lab)
        if not s:
            continue
        p, lo, hi = wilson(s["missed"], s["faults"])
        lut, ff = cost.get(lab, ("-", "-"))
        code = f"{100 * s['code'] / s['faults']:.2f}" if "code" in s else "-"
        clean = "%d/%d" % s["clean"] if "clean" in s else "-"
        print(f"{lab:10s} {lut:>5} {ff:>4} {code:>6s} {s['faults']:6d} "
              f"{100 * p:6.2f} ({100 * lo:5.2f}-{100 * hi:5.2f}) {str(s.get('masked', '-')):>6s} "
              f"{100 * s['intact_alarms'] / max(1, s['intact']):7.0f} {clean:>7s}")

    ours = [lab for lab in stats if re.match(r"v3_i\d+c(_s\d)?$", lab)]
    nf = sum(stats[l]["faults"] for l in ours)
    nm = sum(stats[l]["missed"] for l in ours)
    nk = sum(stats[l]["masked"] for l in ours)
    print(f"\npipelined builds {sorted(ours)}: {nf} faults, {nm} missed, {nk} masked "
          f"(upper 95 % bound on masking {100 * wilson(0, nf)[2]:.3f} %)")
    for lab in sorted(files):
        if len(files[lab]) > 1:
            reps = []
            for f in files[lab]:
                _, runs = load(f)
                sf = [r for r in runs if r["flips"]]
                reps.append((len(sf), sum(1 for r in sf if not r["alarm"])))
            print(f"repeated campaigns {lab}: (faults, missed) = {reps}")

    print("\nFig. 2 points (glitch length ns, % caught | % intact runs with alarm)")
    for lab in FIG2:
        s = stats.get(lab)
        if not s:
            continue
        det, alarm = [], []
        for ph in sorted(s["per_phase"], key=int):
            f, m, ok, oka = s["per_phase"][ph]
            x = int(ph) * STEP_NS
            if f >= args.min_runs:
                det.append(f"({x:.2f},{100 * (f - m) / f:.1f})")
            if ok >= args.min_runs:
                alarm.append(f"({x:.2f},{100 * oka / ok:.1f})")
        print(f"{lab}\n  caught: {' '.join(det)}\n  intact: {' '.join(alarm)}")


if __name__ == "__main__":
    main()
