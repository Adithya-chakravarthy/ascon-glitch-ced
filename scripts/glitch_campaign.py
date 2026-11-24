# Host side of the clock-glitch experiments (needs pyserial).
#
#   python glitch_campaign.py selftest
#   python glitch_campaign.py calibrate --round 5
#   python glitch_campaign.py campaign --config v3_i32c   # 4000 glitches, 3.69 .. 2.89 ns
#   python glitch_campaign.py clean --config v3_i32c      # 2000 runs without glitch
#
# Every glitched run stores the flipped state bits (index 64*w + b), the tag check,
# the alarm flag and the TDC reading as one JSON line.

import argparse
import datetime
import json
import math
import os
import random
import sys
import time
from collections import Counter

import serial
from serial.tools import list_ports

BAUD = 921600
STEP_NS = 1.0 / (56 * 0.6)          # MMCM fine phase step at 600 MHz VCO
DELTA_NS = 0.0
MIN_STEP = 76
MAX_STEP = 327
EARLY_STEP = 168
TDC_TAPS = 384
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "results", "glitch"))
M64 = (1 << 64) - 1
VARIANT_NAMES = {0: "none", 1: "S-box parity", 2: "global parity", 3: "group parity",
                 4: "duplication", 5: "S-box interleaved parity", 6: "S-box CRC-3",
                 7: "S-box CRC-3, registered compare"}
CAMPAIGN_PHASES = "124,121,118,115,112,109,106,103,100,97"


def ns(step):
    return DELTA_NS + step * STEP_NS


def median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


IV = 0x00001000808C0001


def rotr(x, r):
    return ((x >> r) | (x << (64 - r))) & M64


def perm_round(S, r):
    x0, x1, x2, x3, x4 = S
    x2 ^= ((0xF - r) << 4) | r
    x0 ^= x4; x4 ^= x3; x2 ^= x1
    t0 = (~x0 & M64) & x1; t1 = (~x1 & M64) & x2; t2 = (~x2 & M64) & x3
    t3 = (~x3 & M64) & x4; t4 = (~x4 & M64) & x0
    x0 ^= t1; x1 ^= t2; x2 ^= t3; x3 ^= t4; x4 ^= t0
    x1 ^= x0; x0 ^= x4; x3 ^= x2; x2 = ~x2 & M64
    x0 ^= rotr(x0, 19) ^ rotr(x0, 28)
    x1 ^= rotr(x1, 61) ^ rotr(x1, 39)
    x2 ^= rotr(x2, 1) ^ rotr(x2, 6)
    x3 ^= rotr(x3, 10) ^ rotr(x3, 17)
    x4 ^= rotr(x4, 7) ^ rotr(x4, 41)
    return [x0, x1, x2, x3, x4]


def le64(b, i):
    return int.from_bytes(b[8 * i:8 * i + 8], "little")


# expected state after initialization round t (no AD, no plaintext)
def state_after_init_round(key, nonce, t):
    S = [IV, le64(key, 0), le64(key, 1), le64(nonce, 0), le64(nonce, 1)]
    for r in range(t + 1):
        S = perm_round(S, r)
    return S


def tag_empty(key, nonce):
    K0, K1 = le64(key, 0), le64(key, 1)
    S = state_after_init_round(key, nonce, 11)
    S[3] ^= K0; S[4] ^= K1
    S[4] ^= 1 << 63
    S[0] ^= 0x01
    S[2] ^= K0; S[3] ^= K1
    for r in range(12):
        S = perm_round(S, r)
    return (S[3] ^ K0).to_bytes(8, "little") + (S[4] ^ K1).to_bytes(8, "little")


KAT1_TAG = bytes.fromhex("4F9C278211BEC9316BF68F46EE8B2EC6")   # LWC_AEAD_KAT_128_128.txt, Count = 1


class BoardLost(Exception):
    pass


def find_port():
    ports = [p for p in list_ports.comports() if p.vid == 0x0403]
    if not ports:
        sys.exit("no FTDI serial port found, use --port")
    num = lambda n: int("".join(ch for ch in n if ch.isdigit()) or 0)
    return sorted((p.device for p in ports), key=num)[-1]


class Board:
    def __init__(self, port):
        self.phase = 0
        self.ser = serial.Serial(port, BAUD, timeout=0.5)
        time.sleep(0.1)
        self.ser.reset_input_buffer()

    def _cmd(self, data, nreply):
        self.ser.write(data)
        return self.ser.read(nreply)

    def version(self):
        return self._cmd(b"V", 4)

    def variant(self):
        r = self._cmd(b"Q", 1)
        return r[0] if r else None

    def set_phase(self, step):
        r = self._cmd(b"P" + bytes([step & 0xFF, step >> 8]), 1)
        if r != b"K":
            raise BoardLost(f"phase command: reply {r!r}")
        self.phase = step

    def set_glitch(self, t, s, enable):
        en = (1 if enable else 0) | (2 if (enable and self.phase < EARLY_STEP) else 0)
        r = self._cmd(b"G" + bytes([t & 0xFF, t >> 8, s & 0xFF, s >> 8, en]), 1)
        if r != b"K":
            raise BoardLost(f"glitch command: reply {r!r}")

    def encrypt_empty(self, key, nonce):
        r = self._cmd(b"E\x00" + key + nonce, 19)
        if len(r) != 19:
            raise BoardLost(f"encrypt: {len(r)} of 19 reply bytes")
        return r[:16], r[16], r[17] | (r[18] << 8)

    def snapshot(self):
        r = self._cmd(b"S", 40)
        if len(r) != 40:
            raise BoardLost(f"snapshot: {len(r)} of 40 bytes")
        return [le64(r, w) for w in range(5)]

    def tdc(self):
        r = self._cmd(b"T", 49)
        if len(r) != 49:
            raise BoardLost(f"ruler: {len(r)} of 49 bytes")
        pol = r[48] & 1
        return sum(1 for i in range(TDC_TAPS) if ((r[i // 8] >> (i % 8)) & 1) == pol)

    def resync(self):
        for _ in range(6):
            time.sleep(0.05)
            self.ser.reset_input_buffer()
            if self.version() == b"ASC1":
                return True
        return False


def open_board(args):
    port = args.port or find_port()
    print(f"Opening {port} at {BAUD} baud")
    b = Board(port)
    v = b.version()
    if v != b"ASC1":
        sys.exit(f"unexpected board id {v!r} (expected b'ASC1')")
    var = b.variant()
    print(f"board ok, VARIANT {var} ({VARIANT_NAMES.get(var, '?')})")
    try:
        b.set_phase(0)
        b.set_glitch(0, 2, 0)
        b.tdc()
    except BoardLost:
        sys.exit("bitstream does not support the glitch commands P/G/T")
    return b, var


def one_trial(board, rnd, t, base_cycles, with_tdc=False):
    key, nonce = rnd.randbytes(16), rnd.randbytes(16)
    tag, status, cycles = board.encrypt_empty(key, nonce)
    snap = board.snapshot()
    taps = board.tdc() if with_tdc else None
    exp = state_after_init_round(key, nonce, t)
    flips = [64 * w + b for w in range(5) for b in range(64) if ((snap[w] ^ exp[w]) >> b) & 1]
    return {
        "t": t, "key": key.hex(), "nonce": nonce.hex(),
        "tag_ok": tag == tag_empty(key, nonce),
        "alarm": status & 1, "timeout": (status >> 1) & 1,
        "cycles": cycles, "ctrl": int(cycles != base_cycles),
        "flips": flips, "tdc": taps,
    }


def is_faulty(r):
    return bool(r.get("lost") or r["flips"] or not r["tag_ok"] or r["timeout"])


def recover(board, phase, t):
    print("  no reply, resyncing")
    if not board.resync():
        sys.exit("board lost; reprogram the FPGA and rerun (data so far is saved)")
    board.set_phase(phase)
    board.set_glitch(t, t + 2, 1)


def measure_base_cycles(board, rnd):
    board.set_glitch(0, 2, 0)
    _, _, cyc = board.encrypt_empty(rnd.randbytes(16), rnd.randbytes(16))
    return cyc


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def pct(x):
    return f"{100 * x:5.1f}%"


def ci_str(k, n):
    p, lo, hi = wilson(k, n)
    return f"{k}/{n} = {pct(p)}  (95% CI {pct(lo)} - {pct(hi)})"


def escapes_groups(flips, ngroups_fn):
    c = Counter(ngroups_fn(i) for i in flips)
    return all(v % 2 == 0 for v in c.values())


GROUPINGS = [(1, "interleave"), (2, "interleave"), (2, "random"), (4, "interleave"), (4, "random"),
             (5, "word"), (8, "interleave"), (16, "interleave"), (32, "interleave"),
             (64, "interleave"), (128, "interleave"), (8, "random"), (16, "random"), (32, "random"),
             (64, "random"), (128, "random")]


def print_group_escapes(state_f):
    try:
        sys.path.insert(0, HERE)
        from gen_gpar import make_groups
    except ImportError:
        print("(gen_gpar not found, skipping group table)")
        return
    print("\nOffline: escapes if the state were checked with k parity groups")
    print("  grouping      |  faults missed")
    for k, mode in GROUPINGS:
        gid = {}
        for g, members in enumerate(make_groups(k, mode, 1)):
            for i in members:
                gid[i] = g
        esc = sum(1 for r in state_f if escapes_groups(r["flips"], lambda i: gid[i]))
        print(f"  {mode:10s}{k:4d} |  {ci_str(esc, len(state_f))}")


def print_detection(done, faulty, state_f):
    print("\n--- detection ---")
    det_s = sum(1 for r in state_f if r["alarm"])
    det_f = sum(1 for r in faulty if r["alarm"])
    miss_s = len(state_f) - det_s
    false_al = sum(1 for r in done if r["alarm"] and not is_faulty(r))
    print(f"State faults detected : {ci_str(det_s, len(state_f))}")
    print(f"State faults missed   : {ci_str(miss_s, len(state_f))}")
    print(f"Any wrong output detected (incl. control faults): {ci_str(det_f, len(faulty))}")
    print(f"Alarms although the output was correct (glitched runs): {false_al} of {len(done) - len(faulty)}")
    if state_f:
        print("\nMissed state faults by number of flipped bits:")
        for name, lo, hi in [("1 bit", 1, 1), ("2 bits", 2, 2), ("3-4 bits", 3, 4), ("5-8 bits", 5, 8),
                             ("9+ bits", 9, 320)]:
            sub = [r for r in state_f if lo <= len(r["flips"]) <= hi]
            if sub:
                m = sum(1 for r in sub if not r["alarm"])
                print(f"  {name:9s}: {m:5d} missed of {len(sub):5d}")
        phases = sorted(set(r["phase"] for r in done if "phase" in r))
        if len(phases) > 1:
            print("\nPer glitch length:   phase (ns)  |   n  | state faults | missed | alarms w/o fault")
            for p in phases:
                sub = [r for r in done if r.get("phase") == p]
                sf = [r for r in sub if r["flips"]]
                m = sum(1 for r in sf if not r["alarm"])
                fa = sum(1 for r in sub if r["alarm"] and not is_faulty(r))
                print(f"              {p:4d} ({ns(p):4.2f})  | {len(sub):4d} | {len(sf):12d} | {m:6d} | {fa:5d}")
        bitc = Counter(i for r in state_f for i in r["flips"])
        print(f"\nDistinct bits flipped: {len(bitc)} of 320; most often:",
              ", ".join(f"x{i // 64}[{i % 64}]: {c}" for i, c in bitc.most_common(8)))


def summarize(records, variant):
    recs = [r for r in records if not r.get("meta")]
    n = len(recs)
    lost = sum(1 for r in recs if r.get("lost"))
    done = [r for r in recs if not r.get("lost")]
    faulty = [r for r in done if is_faulty(r)]
    state_f = [r for r in done if r["flips"]]
    other_f = [r for r in faulty if not r["flips"]]
    print("\n--- summary ---")
    print(f"Glitched encryptions      : {n}   (board lost: {lost})")
    print(f"Faulty (any wrong output) : {ci_str(len(faulty), len(done))}")
    print(f"  state faults (bits flipped after the glitched round): {len(state_f)}")
    print(f"  other faults (control/round counter/timeouts)       : {len(other_f)}")
    print(f"  timeouts                                            : {sum(r['timeout'] for r in done)}")

    if state_f:
        w = [len(r["flips"]) for r in state_f]
        buckets = [("1 bit", 1, 1), ("2 bits", 2, 2), ("3 bits", 3, 3), ("4-8 bits", 4, 8),
                   ("9-32 bits", 9, 32), ("33+ bits", 33, 320)]
        print("\nHow many bits flipped per state fault:")
        for name, lo, hi in buckets:
            k = sum(1 for x in w if lo <= x <= hi)
            print(f"  {name:10s}: {k:6d}  {pct(k / len(w))}")
        multi = sum(1 for x in w if x >= 2)
        even = sum(1 for x in w if x % 2 == 0)
        print(f"\nMulti-bit faults (>= 2 bits)              : {ci_str(multi, len(w))}")
        print(f"Escape 1 global parity bit (even # flips) : {ci_str(even, len(w))}")
        print_group_escapes(state_f)

        bitc = Counter(i for r in state_f for i in r["flips"])
        print(f"\nDistinct bits that ever flipped: {len(bitc)} of 320")
        print("Most fault-prone bits (bit = x<word>[<bit>]: count):",
              ", ".join(f"x{i // 64}[{i % 64}]: {c}" for i, c in bitc.most_common(10)))
        pairc = Counter()
        for r in state_f:
            f = r["flips"]
            if 2 <= len(f) <= 32:
                for a in range(len(f)):
                    for b in range(a + 1, len(f)):
                        pairc[(f[a], f[b])] += 1
        if pairc:
            print("Bit pairs that flip together most often:",
                  ", ".join(f"x{a // 64}[{a % 64}]+x{b // 64}[{b % 64}]: {c}" for (a, b), c in pairc.most_common(8)))

    for key, label in (("phase", "Phase step"), ("t", "Round")):
        vals = sorted(set(r[key] for r in done if key in r))
        if len(vals) > 1:
            print(f"\n{label:>10s}{'':12s} |     n | ruler | faulty | state faults | multi-bit of state faults")
            for v in vals:
                sub = [r for r in done if r.get(key) == v]
                sf = [r for r in sub if r["flips"]]
                mb = sum(1 for r in sf if len(r["flips"]) >= 2)
                extra = f" ({ns(v):5.2f} ns)" if key == "phase" else " " * 11
                tp = median([r["tdc"] for r in sub if r.get("tdc") is not None])
                print(f"{v:>10}{extra} | {len(sub):5d} | {tp if tp is not None else '-':>5} | "
                      f"{pct(sum(map(is_faulty, sub)) / len(sub))} | "
                      f"{len(sf):12d} | {pct(mb / len(sf)) if sf else '   - '}")

    if variant and variant != 0:
        print_detection(done, faulty, state_f)


def fit_taps(points):
    pts = [(st, tp) for st, tp in points if tp is not None and 5 <= tp <= TDC_TAPS - 5]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx == 0:
        return None
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx
    return slope, (STEP_NS * 1000 / slope if slope > 0 else None)


def mode_selftest(args):
    print("1) host model vs KAT:",
          "OK" if tag_empty(bytes(range(16)), bytes(range(16, 32))) == KAT1_TAG else "FAIL")
    board, var = open_board(args)
    rnd = random.Random(args.seed)
    ok = True
    try:
        K, N = bytes(range(16)), bytes(range(16, 32))
        tag, status, cyc = board.encrypt_empty(K, N)
        good = tag == KAT1_TAG and status == 0
        ok &= good
        print(f"2) KAT on the board: {'OK' if good else 'FAIL'} ({cyc} cycles)")
        base = cyc

        bad = 0
        for t in range(12):
            board.set_glitch(t, t + 2, 0)
            for _ in range(5):
                r = one_trial(board, rnd, t, base)
                if is_faulty(r) or r["ctrl"]:
                    bad += 1
        ok &= bad == 0
        print(f"3) snapshots after init rounds 0..11, glitch off, 60 runs: "
              f"{'OK' if bad == 0 else f'FAIL ({bad} wrong)'}")

        board.set_glitch(5, 7, 0)
        normal = median([one_trial(board, rnd, 5, base, with_tdc=True)["tdc"] for _ in range(5)])
        print(f"4) TDC, normal 10 ns cycle: {normal} of {TDC_TAPS} taps")

        print("5) glitch on round 5, TDC should drop with the glitch length:")
        readings = []
        for step in (327, 268, 202, 168, 134, 101, 76):
            board.set_phase(step)
            board.set_glitch(5, 7, 1)
            res = [one_trial(board, rnd, 5, base, with_tdc=True) for _ in range(15)]
            nf = sum(map(is_faulty, res))
            taps = median([r["tdc"] for r in res])
            readings.append((step, taps))
            print(f"   phase {step:3d} ({ns(step):.2f} ns): {taps:3d} taps, "
                  f"{nf:2d}/15 faulty")
            if step >= 268 and nf:
                ok = False
                print("   ^ unexpected faults at >= 8 ns")
        mono = all(readings[i + 1][1] <= readings[i][1] + 3 for i in range(len(readings) - 1))
        shorter = readings[-1][1] < normal - 10
        fit = fit_taps(readings)
        if shorter and mono:
            print("   TDC reading decreases monotonically: ok")
            if fit and fit[1]:
                print(f"   about {fit[1]:.0f} ps per tap")
        else:
            ok = False
            print("   TDC reading does not follow the glitch length")
    finally:
        try:
            board.set_glitch(0, 2, 0); board.set_phase(0)
        except BoardLost:
            pass
    print("\nselftest passed" if ok else "\nselftest FAILED")


def mode_calibrate(args):
    board, var = open_board(args)
    rnd = random.Random(args.seed)
    os.makedirs(OUT_DIR, exist_ok=True)
    fname = os.path.join(OUT_DIR, datetime.datetime.now().strftime("calibrate_%Y%m%d_%H%M%S.csv"))
    start = min(max(args.start, args.stop), MAX_STEP)
    stop = max(min(args.start, args.stop), MIN_STEP)
    base = measure_base_cycles(board, rnd)
    t = args.round
    rows, hot = [], 0
    print(f"Sweeping phase {start} -> {stop} (step {args.step}), glitching init round {t}, "
          f"{args.n} encryptions per point. Results: {fname}\n")
    print(" step | glitch ns | ruler | faulty | state | mean bits | multi-bit | timeouts | lost")
    try:
        for step in range(start, stop - 1, -args.step):
            try:
                board.set_phase(step)
                board.set_glitch(t, t + 2, 1)
            except BoardLost:
                recover(board, step, t)
            res, lost = [], 0
            for i in range(args.n):
                try:
                    res.append(one_trial(board, rnd, t, base, with_tdc=(i < 3)))
                except BoardLost:
                    lost += 1
                    recover(board, step, t)
            nf = sum(map(is_faulty, res)) + lost
            sf = [r for r in res if r["flips"]]
            mean_w = sum(len(r["flips"]) for r in sf) / len(sf) if sf else 0.0
            multi = sum(1 for r in sf if len(r["flips"]) >= 2)
            to = sum(r["timeout"] for r in res)
            taps = median([r["tdc"] for r in res if r.get("tdc") is not None])
            fr = nf / args.n
            rows.append((step, ns(step), taps if taps is not None else -1, fr, len(sf), mean_w, multi, to, lost))
            print(f"{step:5d} | {ns(step):8.3f}  | {taps if taps is not None else '-':>5} | {pct(fr)} | "
                  f"{len(sf):5d} | {mean_w:9.1f} | "
                  f"{(pct(multi / len(sf)) if sf else '   - '):>9s} | {to:8d} | {lost:4d}")
            hot = hot + 1 if fr >= 0.99 else 0
            if hot >= 3:
                print("three points with ~100 % faults, stopping")
                break
    finally:
        with open(fname, "w") as f:
            f.write("phase_step,glitch_ns,ruler_taps,faulty_frac,state_faults,mean_bits,multi_bit,timeouts,lost\n")
            for r in rows:
                f.write(",".join(f"{x:.4f}" if isinstance(x, float) else str(x) for x in r) + "\n")
        try:
            board.set_glitch(0, 2, 0); board.set_phase(0)
        except BoardLost:
            pass

    fit = fit_taps([(r[0], r[2]) for r in rows if r[2] >= 0])
    if fit and fit[1]:
        print(f"\nTDC: {fit[0]:.2f} taps per phase step, about {fit[1]:.0f} ps per tap")
    faulty_rows = [r for r in rows if r[3] >= 0.05]
    if not faulty_rows:
        print("\nno faults in the sweep")
        return
    onset = faulty_rows[0][0]
    mid = min(rows, key=lambda r: abs(r[3] - 0.5))[0]
    strong = min((r for r in rows if r[3] < 0.99), key=lambda r: abs(r[3] - 0.9), default=rows[-1])[0]
    picks = sorted({onset, mid, strong}, reverse=True)
    print(f"\nfaults start at phase step {onset} ({ns(onset):.2f} ns)")
    print(f"suggested phases: {','.join(map(str, picks))}")


def parse_rounds(s):
    out = set()
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    bad = [t for t in out if not 1 <= t <= 11]
    if bad:
        sys.exit(f"rounds must be 1..11, got {bad}")
    return sorted(out)


def mode_pilot(args):
    phases = [int(x) for x in args.phases.split(",")]
    for p in phases:
        if not MIN_STEP <= p <= MAX_STEP:
            sys.exit(f"phase {p} outside {MIN_STEP}..{MAX_STEP}")
    rounds = parse_rounds(args.rounds)
    board, var = open_board(args)
    rnd = random.Random(args.seed)
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = args.name if args.name else (args.config if args.config != "-" else "pilot")
    fname = os.path.join(OUT_DIR, f"{name}_v{var}_{stamp}.jsonl")
    check_config(args.config, var)
    base = measure_base_cycles(board, rnd)
    cells = [(p, t) for p in phases for t in rounds]
    print(f"{args.n} glitches, phases {phases}, rounds {rounds}, VARIANT {var}. Saving to {fname}")
    records = [{"meta": True, "variant": var, "phases": phases, "rounds": rounds, "seed": args.seed,
                "base_cycles": base, "date": stamp, "config": args.config}]
    t0, next_report = time.time(), 200
    with open(fname, "w") as f:
        f.write(json.dumps(records[0]) + "\n")
        try:
            i = 0
            while i < args.n:
                rnd.shuffle(cells)
                for p, t in cells:
                    if i >= args.n:
                        break
                    try:
                        board.set_phase(p)
                        board.set_glitch(t, t + 2, 1)
                    except BoardLost:
                        recover(board, p, t)
                    for _ in range(min(args.k, args.n - i)):
                        try:
                            r = one_trial(board, rnd, t, base, with_tdc=not args.no_tdc)
                        except BoardLost:
                            r = {"t": t, "lost": True, "flips": [], "tag_ok": False, "alarm": 0,
                                 "timeout": 0, "cycles": -1, "ctrl": 1}
                            recover(board, p, t)
                        r["phase"] = p
                        r["i"] = i
                        records.append(r)
                        f.write(json.dumps(r) + "\n")
                        i += 1
                    if i >= next_report:
                        print(f"  {i}/{args.n} done ({time.time() - t0:.0f} s)")
                        f.flush()
                        next_report += 200
        finally:
            try:
                board.set_glitch(0, 2, 0); board.set_phase(0)
            except BoardLost:
                pass
    summarize(records, var)
    print(f"\nsaved {fname}")


CONFIG_VARIANT = {"v0": 0, "v1": 1, "v2": 2, "v3": 3, "v4": 4, "v5": 5, "v6": 6, "v7": 7}


def check_config(config, var):
    if config in (None, "-"):
        return
    want = CONFIG_VARIANT.get(config.split("_")[0][:2])
    if want is not None and want != var:
        sys.exit(f"--config {config} expects VARIANT {want}, board reports VARIANT {var}")


def mode_clean(args):
    board, var = open_board(args)
    check_config(args.config, var)
    rnd = random.Random(args.seed)
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = args.config if args.config != "-" else "clean"
    fname = os.path.join(OUT_DIR, f"clean_{name}_v{var}_{stamp}.jsonl")
    board.set_phase(0)
    base = measure_base_cycles(board, rnd)
    meta = {"meta": True, "clean": True, "variant": var, "config": args.config, "seed": args.seed,
            "base_cycles": base, "date": stamp}
    alarms = wrong = 0
    t0 = time.time()
    with open(fname, "w") as f:
        f.write(json.dumps(meta) + "\n")
        for i in range(args.n):
            t = rnd.randint(1, 11)
            board.set_glitch(t, t + 2, 0)
            r = one_trial(board, rnd, t, base)
            r["i"] = i
            r["clean"] = True
            f.write(json.dumps(r) + "\n")
            alarms += r["alarm"]
            wrong += int(is_faulty(r) or bool(r["ctrl"]))
            if (i + 1) % 500 == 0:
                print(f"  {i + 1}/{args.n} done ({time.time() - t0:.0f} s)")
    print("\n--- clean run (no glitch) ---")
    print(f"VARIANT {var} ({VARIANT_NAMES.get(var, '?')}), config {args.config}")
    print(f"Encryptions               : {args.n}")
    print(f"Wrong outputs             : {wrong}")
    print(f"False alarms              : {ci_str(alarms, args.n)}")
    print("ok" if alarms == 0 and wrong == 0 else "PROBLEM")
    print(f"saved {fname}")


def mode_analyze(args):
    with open(args.file) as f:
        records = [json.loads(line) for line in f if line.strip()]
    meta = next((r for r in records if r.get("meta")), {})
    print(f"File {args.file}: VARIANT {meta.get('variant')}, phases {meta.get('phases')}, rounds {meta.get('rounds')}")
    summarize(records, meta.get("variant", 0))


def main():
    ap = argparse.ArgumentParser(description="Clock-glitch experiments on the Ascon core")
    sub = ap.add_subparsers(dest="mode", required=True)

    def common(p):
        p.add_argument("--port", default=None, help="e.g. COM4 (auto-detected if omitted)")
        p.add_argument("--seed", type=int, default=1)

    p = sub.add_parser("selftest"); common(p)
    p = sub.add_parser("calibrate"); common(p)
    p.add_argument("--round", type=int, default=5, help="init round to glitch (1..11)")
    p.add_argument("--start", type=int, default=268, help="first phase step (268 = 8 ns)")
    p.add_argument("--stop", type=int, default=MIN_STEP, help="last phase step")
    p.add_argument("--step", type=int, default=3)
    p.add_argument("--n", type=int, default=40, help="encryptions per phase step")
    p = sub.add_parser("pilot"); common(p)
    p.add_argument("--phases", required=True, help="comma-separated phase steps")
    p.add_argument("--rounds", default="1-11", help="init rounds to glitch, e.g. 1-11 or 2,5,8")
    p.add_argument("--n", type=int, default=1000, help="total number of glitches")
    p.add_argument("--k", type=int, default=5, help="glitches per (phase, round) before switching")
    p.add_argument("--name", default=None, help="output file prefix")
    p.add_argument("--no-tdc", action="store_true", help="skip the TDC readout")
    p.add_argument("--config", default="-", help="build label, e.g. v3_i32c")
    p = sub.add_parser("campaign", help="detector campaign used in the paper"); common(p)
    p.add_argument("--config", required=True, help="build label, e.g. v3_i32c")
    p.add_argument("--phases", default=CAMPAIGN_PHASES)
    p.add_argument("--rounds", default="1-11")
    p.add_argument("--n", type=int, default=4000)
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--name", default=None)
    p.add_argument("--no-tdc", action="store_true")
    p = sub.add_parser("clean", help="runs without glitch (false alarms)"); common(p)
    p.add_argument("--config", default="-", help="build label")
    p.add_argument("--n", type=int, default=2000)
    p = sub.add_parser("analyze")
    p.add_argument("file")
    args = ap.parse_args()
    if args.mode == "calibrate" and not 1 <= args.round <= 11:
        sys.exit("--round must be 1..11")
    if args.mode == "campaign" and args.seed == 1:
        args.seed = 7      # same glitch sequence for every build
    {"selftest": mode_selftest, "calibrate": mode_calibrate, "pilot": mode_pilot,
     "campaign": mode_pilot, "clean": mode_clean, "analyze": mode_analyze}[args.mode](args)


if __name__ == "__main__":
    main()
