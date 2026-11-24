# Fault statistics of glitch logs from the unprotected core.
#   python fault_analysis.py ../data/glitch/campaignA_v0_*.jsonl

import glob, json, math, sys
from collections import Counter, defaultdict

STEP_PS = 1000.0 / (56 * 0.6)
TAP_PS = 17.7

def load(path):
    recs = [json.loads(l) for l in open(path) if l.strip()]
    meta = next((r for r in recs if r.get("meta")), {})
    return meta, [r for r in recs if not r.get("meta")]

def name(i):
    return f"x{i // 64}[{i % 64}]"

def wilson(k, n, z=1.96):
    if n == 0: return (0, 0, 1)
    p = k / n; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0, c - h), min(1, c + h)

def pct(k, n):
    p, lo, hi = wilson(k, n)
    return f"{100*p:5.1f}% [{100*lo:4.1f}-{100*hi:4.1f}]"

def faults(recs):
    return [r for r in recs if not r.get("lost") and r["flips"]]

def escapes(flips, gid):
    c = Counter(gid[i] for i in flips)
    return all(v % 2 == 0 for v in c.values())

def missed(fl, gid):
    return sum(1 for f in fl if escapes(f, gid))

def summary(path):
    meta, recs = load(path)
    done = [r for r in recs if not r.get("lost")]
    F = faults(done)
    other = [r for r in done if not r["flips"] and (not r["tag_ok"] or r["timeout"])]
    w = [len(r["flips"]) for r in F]
    print(f"\n=== {path}: seed {meta.get('seed')}, phases {meta.get('phases')}, {len(recs)} glitches ===")
    print(f"faulty {pct(len(F)+len(other), len(done))} | state faults {len(F)} | other {len(other)} | "
          f"timeouts {sum(r['timeout'] for r in done)} | lost {len(recs)-len(done)} | ctrl {sum(r['ctrl'] for r in done)}")
    print(f"multi-bit {pct(sum(x>=2 for x in w), len(w))} | even (escape global parity) {pct(sum(x%2==0 for x in w), len(w))} | mean bits {sum(w)/len(w):.2f}")
    bits = Counter(i for r in F for i in r["flips"])
    words = Counter(i // 64 for r in F for i in r["flips"])
    print(f"distinct bits {len(bits)} | flips per word {dict(sorted(words.items()))}")
    print("phase | glitch ns | n | ruler taps mean+-sd (ps sd) | faulty | multi-bit")
    for ph in sorted({r["phase"] for r in done}):
        sub = [r for r in done if r["phase"] == ph]
        t = [r["tdc"] for r in sub if r.get("tdc") is not None]
        m = sum(t)/len(t); sd = math.sqrt(sum((x-m)**2 for x in t)/(len(t)-1))
        sf = [r for r in sub if r["flips"]]
        print(f"{ph:5d} | {ph*STEP_PS/1000:8.3f} | {len(sub):4d} | {m:6.1f} +- {sd:4.2f} ({sd*TAP_PS:4.1f}) | "
              f"{pct(len(sf), len(sub))} | {pct(sum(len(r['flips'])>=2 for r in sf), len(sf)) if sf else '-'}")
    return F, bits

if __name__ == "__main__":
    allbits = Counter()
    paths = [f for a in sys.argv[1:] for f in (sorted(glob.glob(a)) or [a])]
    for p in paths:
        F, bits = summary(p)
        allbits += bits
    print("\nbits that flipped (count):")
    print(", ".join(f"{name(i)}:{c}" for i, c in allbits.most_common()))
