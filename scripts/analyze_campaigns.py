# Summarizes the detector campaigns: detection per build, masked faults (computed from the
# flipped bits), checker timing margins, cost and STA vs measured faults.
#
#   python analyze_campaigns.py            # reads ../data, writes ../results/summary.md

import argparse
import csv
import glob
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STEP_NS = 1.0 / (56 * 0.6)
NAME_RE = re.compile(r"^(v\d[A-Za-z0-9_]*?)_v(\d)_(\d{8}_\d{6})\.jsonl$")
CLEAN_RE = re.compile(r"^clean_(v\d[A-Za-z0-9_]*?)_v(\d)_(\d{8}_\d{6})\.jsonl$")
DESC = {
    "v0": "no detector", "v1_sbox": "S-box parity (1 bit per S-box)", "v2_rpar": "global parity",
    "v3_i32": "32 interleaved parity groups", "v3_sta24": "24 slowest bits exact + 1 parity",
    "v3_sta32": "32 slowest bits exact + 1 parity", "v4_dup": "duplication",
    "v3_i32c": "32 interleaved groups, pipelined prediction",
    "v3_i8c": "8 interleaved groups, pipelined prediction",
    "v3_i16c": "16 interleaved groups, pipelined prediction",
    "v3_i64c": "64 interleaved groups, pipelined prediction",
    "v5_sbox2": "S-box interleaved parity (2 bits per S-box)",
    "v6_crc3": "S-box CRC-3 (3 bits per S-box)",
    "v7_crc3r": "S-box CRC-3, signatures registered, compared next cycle",
}


def desc(label):
    base = label.split("__")[0]
    m2 = re.match(r"(.*)_s(\d)$", base)
    if base not in DESC and m2 and m2.group(1) in DESC:
        return DESC[m2.group(1)] + f" [placement {m2.group(2)}]"
    if base in DESC:
        return DESC[base]
    m = re.match(r"v3_fix(\d+)_p(\d+)", base)
    if m:
        return f"{m.group(1)} slowest bits exact, reselected (pass {m.group(2)}), pipelined prediction"
    return base


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def rate(k, n, digits=2):
    p, lo, hi = wilson(k, n)
    f = f"{{:.{digits}f}}"
    return f"{k}/{n} = {f.format(100 * p)} % (95 % CI {f.format(100 * lo)}-{f.format(100 * hi)} %)"


def bitname(i):
    return f"x{i // 64}[{i % 64}]"


def load_jsonl(path):
    with open(path) as f:
        recs = [json.loads(line) for line in f if line.strip()]
    meta = next((r for r in recs if r.get("meta")), {})
    return meta, [r for r in recs if not r.get("meta")]


def newest(pattern_re, folder):
    out = {}
    for f in sorted(glob.glob(os.path.join(folder, "*.jsonl"))):
        m = pattern_re.match(os.path.basename(f))
        if m:
            out[m.group(1)] = (f, int(m.group(2)))
    return out


def load_slack(path):
    if not path or not os.path.exists(path):
        return None
    s = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            s[int(row["bit_index"])] = float(row["datapath_slack_ns"])
    return s if s else None


def load_checker(path):
    if not path or not os.path.exists(path):
        return []
    with open(path) as f:
        return [(r["register"], int(r["index"]), float(r["slack_ns"])) for r in csv.DictReader(f)]


def load_groups(path):
    with open(path) as f:
        d = json.load(f)
    return [sorted(g) for g in (d["groups"] if isinstance(d, dict) else d)]


def ranks(slack):
    order = sorted(slack, key=lambda i: (slack[i], i))
    return {b: k + 1 for k, b in enumerate(order)}


def auc_low_slack(slack, positives):
    pos = [slack[i] for i in slack if i in positives]
    neg = [slack[i] for i in slack if i not in positives]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for q in neg:
            wins += 1.0 if p < q else (0.5 if p == q else 0.0)
    return wins / (len(pos) * len(neg))


def ideal_detect_fn(variant, groups):
    if variant == 2:
        return lambda flips: len(flips) % 2 == 1
    if variant == 3 and groups:
        gid = {i: g for g, m in enumerate(groups) for i in m}
        return lambda flips: any(v % 2 for v in Counter(gid[i] for i in flips).values())
    if variant == 4:
        return lambda flips: len(flips) > 0
    return None


def analyze_build(label, camp, clean, sta_dir, groups_dir, v0_slack, out):
    P = out.append
    path, var = camp
    meta, recs = load_jsonl(path)
    done = [r for r in recs if not r.get("lost")]
    is_f = lambda r: bool(r["flips"] or not r["tag_ok"] or r["timeout"])
    faulty = [r for r in done if is_f(r)]
    state_f = [r for r in done if r["flips"]]
    det = sum(1 for r in state_f if r["alarm"])
    miss = len(state_f) - det
    false_g = sum(1 for r in done if r["alarm"] and not is_f(r))
    res = {"label": label, "variant": var, "n": len(recs), "lost": len(recs) - len(done),
           "state_faults": len(state_f), "missed": miss, "false_glitched": false_g}

    P(f"\n## {label}  (VARIANT {var}: {desc(label)})")
    P(f"- Campaign file: `{os.path.basename(path)}` (seed {meta.get('seed')}, config {meta.get('config')})")
    P(f"- Glitches: {len(recs)} (board lost {res['lost']}); faulty outputs {len(faulty)}; "
      f"state faults {len(state_f)}; timeouts {sum(r['timeout'] for r in done)}")
    if var != 0:
        P(f"- State faults missed: {rate(miss, len(state_f))}")
        P(f"- Any wrong output detected: {rate(sum(1 for r in faulty if r['alarm']), len(faulty))}")
        P(f"- Alarms with a correct output (glitched runs): {false_g} of {len(done) - len(faulty)}")
    if clean:
        cm, cr = load_jsonl(clean[0])
        fa = sum(r["alarm"] for r in cr)
        wrong = sum(1 for r in cr if r["flips"] or not r["tag_ok"] or r["timeout"] or r.get("ctrl"))
        res["clean_n"], res["clean_false"] = len(cr), fa
        P(f"- No-glitch run: {len(cr)} encryptions, false alarms {rate(fa, len(cr))}, wrong outputs {wrong}")
    else:
        P("- No-glitch run: missing")

    phases = sorted(set(r["phase"] for r in done if "phase" in r))
    if var != 0 and phases:
        P("\n| glitch (ns) | glitches | state faults | multi-bit | missed | runs w/o fault | alarms on them |")
        P("|---|---|---|---|---|---|---|")
        for p in phases:
            sub = [r for r in done if r.get("phase") == p]
            sf = [r for r in sub if r["flips"]]
            mb = sum(1 for r in sf if len(r["flips"]) >= 2)
            m = sum(1 for r in sf if not r["alarm"])
            ok = [r for r in sub if not is_f(r)]
            P(f"| {p * STEP_NS:.2f} | {len(sub)} | {len(sf)} | {mb} | {m} | {len(ok)} | {sum(1 for r in ok if r['alarm'])} |")

    groups = None
    gfile = os.path.join(groups_dir, f"{label}_groups.json")
    if var == 3:
        if os.path.exists(gfile):
            groups = load_groups(gfile)
        else:
            P(f"- (no groups file {os.path.basename(gfile)})")
    if var == 2:
        groups = [list(range(320))]
    ideal = ideal_detect_fn(var, groups)
    if ideal and state_f:
        a = b = c = d = 0
        masked_by_phase = Counter()
        for r in state_f:
            i, h = ideal(r["flips"]), bool(r["alarm"])
            if i and h: a += 1
            elif i and not h: b += 1; masked_by_phase[r.get("phase")] += 1
            elif h: c += 1
            else: d += 1
        res["masked"], res["code_limit"] = b, d
        P("\nIdeal checker (from the flipped bits) vs. the alarm on the board:")
        P(f"- covered by the code, alarm: {a}")
        P(f"- covered by the code, no alarm (masked): {b}"
          + (f"  by phase: {dict(sorted(masked_by_phase.items()))}" if b else ""))
        P(f"- not covered (even flips in every group), alarm: {c}")
        P(f"- not covered, no alarm (code limit): {d}")
        if var == 3:
            cands = {}
            for f in glob.glob(os.path.join(groups_dir, "*_groups.json")):
                fn = ideal_detect_fn(3, load_groups(f))
                cands[os.path.basename(f)[:-12]] = sum(1 for r in state_f if fn(r["flips"]) == bool(r["alarm"])) / len(state_f)
            best = max(cands, key=cands.get) if cands else None
            if best and best.split("__")[0] != label.split("__")[0] and cands[best] > cands.get(label, 0) + 0.05:
                P(f"- warning: alarms match the groups of '{best}' ({100 * cands[best]:.1f} %) better than "
                  f"'{label}' ({100 * cands.get(label, 0):.1f} %)")

    slack = load_slack(os.path.join(sta_dir, f"{label}_state.csv"))
    flipped = Counter(i for r in state_f for i in r["flips"])
    if slack:
        rk = ranks(slack)
        auc = auc_low_slack(slack, set(flipped))
        res["auc"] = auc
        if flipped:
            P(f"\nSTA of this build vs. flipped bits: {len(flipped)} distinct bits flipped; "
              f"AUC (low slack -> flips) = {auc:.3f}" if auc is not None else "")
            top = sorted(flipped, key=lambda i: rk[i])
            P("- flipped bits by Vivado rank (1 = slowest): " +
              ", ".join(f"{bitname(i)}#{rk[i]}({flipped[i]})" for i in top[:25]) + (" ..." if len(top) > 25 else ""))
            P(f"- slowest-ranked bit that flipped: #{max(rk[i] for i in flipped)}")
        if groups and var == 3:
            singles = sorted(g[0] for g in groups if len(g) == 1)
            if singles:
                n = len(singles)
                this_top = set(sorted(slack, key=lambda i: slack[i])[:n])
                ov = len(this_top & set(singles))
                res["overlap"] = f"{ov}/{n}"
                P(f"- {ov} of the {n} exactly checked bits are among the {n} slowest in this placement; "
                  f"worst rank of a checked bit: #{max(rk[i] for i in singles)}")
                outside = [i for i in flipped if i not in singles]
                P(f"- flipped bits not checked exactly: {len(outside)} "
                  + ("(" + ", ".join(f"{bitname(i)}({flipped[i]})" for i in sorted(outside, key=lambda i: -flipped[i])[:10]) + ")" if outside else ""))
        if v0_slack:
            common = set(slack) & set(v0_slack)
            r_old = ranks({i: v0_slack[i] for i in common})
            r_new = ranks({i: slack[i] for i in common})
            for n in (24, 40):
                a_ = {i for i in common if r_old[i] <= n}
                b_ = {i for i in common if r_new[i] <= n}
                P(f"- slowest {n} bits: {len(a_ & b_)} of {n} shared with v0")
    else:
        P(f"\n(no timing file {label}_state.csv)")

    chk = load_checker(os.path.join(sta_dir, f"{label}_checker.csv"))
    if chk and slack:
        if var == 3 and groups:
            pred = {i: s for (reg, i, s) in chk if "pred_q" in reg and i >= 0}
            chunk_regs = {i: s for (reg, i, s) in chk if "chunk_q" in reg and i >= 0}
            if chunk_regs:
                try:
                    cg = json.load(open(gfile)).get("chunk_group") or []
                except (OSError, ValueError):
                    cg = []
                pred = {}
                for ci, sl in chunk_regs.items():
                    if ci < len(cg):
                        pred[cg[ci]] = min(sl, pred.get(cg[ci], sl))
                P(f"\n(pipelined prediction: {len(chunk_regs)} chunk registers, group slack = slowest chunk)")
            margins = []
            for g, m in enumerate(groups):
                if g in pred:
                    margins.append((pred[g] - min(slack[i] for i in m if i in slack), g))
            if margins:
                margins.sort()
                neg = [x for x in margins if x[0] < 0]
                res["margin"] = margins[0][0]
                P(f"\nChecker timing ({len(pred)} groups): margin = slack(prediction) - slack(slowest bit of the group). "
                  f"Smallest margin {margins[0][0]:+.3f} ns (group {margins[0][1]}); "
                  f"groups with negative margin: {len(neg)}")
        elif var in (1, 2, 5, 6, 7):
            key = "pred_q" if var == 2 else ("sp_q/sa_q" if var == 7 else "err_q")
            regs = [s for (reg, _, s) in chk if any(k in reg for k in key.split("/"))] or [s for (_, _, s) in chk]
            worst = min(regs)
            res["margin"] = worst - min(slack.values())
            P(f"\nChecker timing: worst {key} register slack {worst:.3f} ns vs slowest state bit "
              f"{min(slack.values()):.3f} ns -> margin {res['margin']:+.3f} ns "
              f"({'checker faster than data' if res['margin'] > 0 else 'checker slower than data'})")
        if var in (2, 3):
            others = [(reg, i, s) for (reg, i, s) in chk if "pred_q" not in reg and "chunk_q" not in reg]
            if others:
                reg, i, s = min(others, key=lambda x: x[2])
                P(f"- compare/valid registers: worst {reg}"
                  f"{f'[{i}]' if i >= 0 else ''} slack {s:.3f} ns")
    if var == 4:
        sb = load_slack(os.path.join(sta_dir, f"{label}_coreb.csv"))
        if sb and slack:
            ta = set(sorted(slack, key=lambda i: slack[i])[:30])
            tb = set(sorted(sb, key=lambda i: sb[i])[:30])
            P(f"\nDuplication: the 30 slowest bits of core A and core B share {len(ta & tb)} bits")
    return res


def cost_table(path, out):
    P = out.append
    if not os.path.exists(path):
        P(f"\n(no cost file {path})")
        return {}
    rows = list(csv.DictReader(open(path)))
    by = defaultdict(list)
    for r in rows:
        by[r["label"].split("__")[0]].append(r)
    base = {}
    for r in rows:
        if r["label"].split("__")[0] == "v0":
            base[r["strategy"]] = r

    def num(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    P("\n## Cost (Ascon core incl. checker, and checker alone)")
    P("| build | placements | core LUTs (impl) mean / min-max | +LUTs vs v0 (same placement) mean ± sd | "
      "checker LUTs (impl) | core LUTs (synth) | checker LUTs (synth) | +FFs | WNS (ns) |")
    P("|---|---|---|---|---|---|---|---|---|")
    summ = {}
    labels = [k for k in DESC if k in by] + sorted(k for k in by if k not in DESC)
    for lab in labels:
        rs = by.get(lab)
        if not rs:
            continue
        cl = [num(r["impl_core_luts"]) for r in rs if num(r["impl_core_luts"]) is not None]
        dl = []
        dff = []
        for r in rs:
            b = base.get(r["strategy"])
            if b and num(b["impl_core_luts"]) is not None and num(r["impl_core_luts"]) is not None:
                dl.append(num(r["impl_core_luts"]) - num(b["impl_core_luts"]))
                dff.append(num(r["impl_core_ffs"]) - num(b["impl_core_ffs"]))
        kl = [num(r["impl_checker_luts"]) for r in rs if num(r["impl_checker_luts"]) is not None]
        sl = sorted(set(r["synth_core_luts"] for r in rs))
        sk = sorted(set(r["synth_checker_luts"] for r in rs))
        wns = [float(r["wns_ns"]) for r in rs if r["wns_ns"] not in ("", "?")]
        sd = f" ± {statistics.stdev(dl):.0f}" if len(dl) > 1 else ""
        P(f"| {lab} | {len(rs)} | {statistics.mean(cl):.0f} / {min(cl)}-{max(cl)} | "
          f"{(f'{statistics.mean(dl):+.0f}' + sd) if dl else '-'} | "
          f"{(f'{statistics.mean(kl):.0f}') if kl else '-'} | {'/'.join(sl)} | {'/'.join(sk)} | "
          f"{(f'{statistics.mean(dff):+.0f}') if dff else '-'} | {min(wns):.3f} |")
        summ[lab] = (statistics.mean(dl) if dl else None)
    return summ


def stability(sta_dir, groups_dir, out):
    files = sorted(glob.glob(os.path.join(sta_dir, "*_state.csv")))
    if len(files) < 2:
        return
    sel_file = os.path.join(groups_dir, "sta_sel24.json")
    chosen = None
    if os.path.exists(sel_file):
        chosen = {g[0] for g in load_groups(sel_file) if len(g) == 1}
    P = out.append
    P("\n## Slowest bits per placement vs. the 24 bits selected from v0")
    P("| build | 24 slowest in selection | 40 slowest in selection |")
    P("|---|---|---|")
    for f in files:
        s = load_slack(f)
        if not s or not chosen:
            continue
        order = sorted(s, key=lambda i: s[i])
        P(f"| {os.path.basename(f)[:-10]} | {len(set(order[:24]) & chosen)}/24 | {len(set(order[:40]) & chosen)}/24 |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glitch", default=os.path.join(ROOT, "data", "glitch"))
    ap.add_argument("--sta", default=os.path.join(ROOT, "data", "sta"))
    ap.add_argument("--groups", default=os.path.join(ROOT, "data", "groups"))
    ap.add_argument("--cost", default=os.path.join(ROOT, "data", "cost.csv"))
    ap.add_argument("--v0-slack", default=os.path.join(ROOT, "data", "sta", "v0_state.csv"))
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "summary.md"))
    args = ap.parse_args()

    camps = newest(NAME_RE, args.glitch)
    cleans = newest(CLEAN_RE, args.glitch)
    if not camps:
        sys.exit(f"no campaign files (v*_v*_*.jsonl) in {args.glitch}")
    v0_slack = load_slack(args.v0_slack)
    out = ["# Detector campaigns", ""]
    if not v0_slack:
        out.append(f"(no v0 slack file {args.v0_slack})")
    results = []
    order = [k for k in DESC if k in camps] + sorted(k for k in camps if k not in DESC)
    for label in order:
        results.append(analyze_build(label, camps[label], cleans.get(label), args.sta, args.groups, v0_slack, out))
    cost = cost_table(args.cost, out)
    stability(args.sta, args.groups, out)

    out.append("\n## Overview")
    out.append("| detector | +core LUTs vs v0 | state faults | missed (95 % CI) | checker masked | false alarms (clean) |")
    out.append("|---|---|---|---|---|---|")
    for r in results:
        if r["variant"] == 0:
            continue
        p, lo, hi = wilson(r["missed"], r["state_faults"])
        c = cost.get(r["label"])
        out.append(f"| {desc(r['label'])} | {('%+.0f' % c) if c is not None else '-'} | "
                   f"{r['state_faults']} | {r['missed']} ({100 * p:.2f} %, {100 * lo:.2f}-{100 * hi:.2f} %) | "
                   f"{r.get('masked', '-')} | {r.get('clean_false', '-')}/{r.get('clean_n', '-')} |")

    text = "\n".join(out) + "\n"
    print(text)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"saved {args.out}")


if __name__ == "__main__":
    main()
