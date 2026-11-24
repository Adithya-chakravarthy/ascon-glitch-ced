# Functional test of the board over UART: official KATs plus random encryptions
# compared with the Python reference (ascon_ref.py).

import argparse
import os
import random
import sys
import time

import serial
from serial.tools import list_ports

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ascon_ref import ascon_encrypt

BAUD = 921600


def find_port():
    ports = [p for p in list_ports.comports() if p.vid == 0x0403]
    if not ports:
        sys.exit("no FTDI serial port found, use --port")
    num = lambda n: int("".join(ch for ch in n if ch.isdigit()) or 0)
    return sorted((p.device for p in ports), key=num)[-1]


class AsconBoard:
    def __init__(self, port, baud=BAUD):
        self.ser = serial.Serial(port, baud, timeout=1)
        time.sleep(0.1)
        self.ser.reset_input_buffer()

    def version(self):
        self.ser.write(b"V")
        return self.ser.read(4)

    def variant(self):
        self.ser.write(b"Q")
        r = self.ser.read(1)
        return r[0] if r else None

    def encrypt(self, key, nonce, ad, pt):
        assert len(ad) % 16 == 0 and len(pt) % 16 == 0
        na, npb = len(ad) // 16, len(pt) // 16
        assert na <= 4 and npb <= 4
        self.ser.write(b"E" + bytes([(na << 4) | npb]) + key + nonce + ad + pt)
        n = 16 * npb + 16 + 3
        resp = self.ser.read(n)
        if len(resp) != n:
            raise IOError(f"expected {n} reply bytes, got {len(resp)}: {resp.hex()}")
        ct, tag = resp[:16 * npb], resp[16 * npb:16 * npb + 16]
        status, cycles = resp[-3], resp[-2] | (resp[-1] << 8)
        return ct, tag, status, cycles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=BAUD)
    ap.add_argument("--n", type=int, default=1000, help="number of random tests")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    port = args.port or find_port()
    print(f"Opening {port} at {args.baud} baud")
    board = AsconBoard(port, args.baud)

    v = board.version()
    if v != b"ASC1":
        sys.exit(f"unexpected board id {v!r}")
    names = {0: "none", 1: "S-box parity", 2: "global parity", 3: "group parity", 4: "duplication",
             5: "S-box interleaved parity", 6: "S-box CRC-3", 7: "S-box CRC-3, registered"}
    var = board.variant()
    print(f"VARIANT {var} ({names.get(var, '?')})")

    K, N, h = bytes(range(16)), bytes(range(0x10, 0x20)), bytes.fromhex
    kats = [(1, b"", b"", "4F9C278211BEC9316BF68F46EE8B2EC6"),
            (545, h("303132333435363738393A3B3C3D3E3F"), h("202122232425262728292A2B2C2D2E2F"),
             "6373EBB28BE97C9BAC090CF399C13EF13ABFC0D209E8F4844C90814D13F32C59"),
            (1057, b"", h("202122232425262728292A2B2C2D2E2F303132333435363738393A3B3C3D3E3F"),
             "E8C3DEEE246CC5EAE3E872313897A2BB6089AA3E15E80307970F2D1F006654C2AAA5FA172CB9F07D07463CEFC7440BC1")]
    for count, ad, pt, exp in kats:
        ct, tag, status, cyc = board.encrypt(K, N, ad, pt)
        ok = (ct + tag).hex().upper() == exp
        print(f"KAT Count={count:<5d}: {'OK' if ok else 'FAIL'} ({cyc} cycles)")

    rnd = random.Random(args.seed)
    bad, alarms, t0 = 0, 0, time.time()
    for i in range(args.n):
        key, nonce = rnd.randbytes(16), rnd.randbytes(16)
        ad, pt = rnd.randbytes(16 * rnd.randint(0, 4)), rnd.randbytes(16 * rnd.randint(0, 4))
        ref = ascon_encrypt(key, nonce, ad, pt)
        ct, tag, status, cyc = board.encrypt(key, nonce, ad, pt)
        alarms += status & 1
        if ct + tag != ref or status != 0:
            bad += 1
            if bad <= 5:
                print(f"  mismatch in test {i}: AD={len(ad)//16} PT={len(pt)//16} status={status}")
    dt = time.time() - t0
    print(f"{args.n - bad}/{args.n} random encryptions match the reference "
          f"({args.n / dt:.0f} encryptions/s over USB)")
    print(f"false alarms: {alarms}")
    print("PASS" if bad == 0 else "FAIL")


if __name__ == "__main__":
    main()
