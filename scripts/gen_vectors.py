# Test vectors for sim/tb_ascon_aead128.v (official KATs + random messages).

import argparse
import os
import random
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--n", type=int, default=200, help="number of random tests")
parser.add_argument("--seed", type=int, default=1)
parser.add_argument("--out", default="vectors.txt")
args = parser.parse_args()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ascon_ref import ascon_encrypt

KEY = bytes(range(16))
NONCE = bytes(range(0x10, 0x20))
h = bytes.fromhex
OFFICIAL = [
    (1, b"", b"", "4F9C278211BEC9316BF68F46EE8B2EC6"),
    (545, h("303132333435363738393A3B3C3D3E3F"), h("202122232425262728292A2B2C2D2E2F"),
     "6373EBB28BE97C9BAC090CF399C13EF13ABFC0D209E8F4844C90814D13F32C59"),
    (1057, b"", h("202122232425262728292A2B2C2D2E2F303132333435363738393A3B3C3D3E3F"),
     "E8C3DEEE246CC5EAE3E872313897A2BB6089AA3E15E80307970F2D1F006654C2AAA5FA172CB9F07D07463CEFC7440BC1"),
]
for count, ad, pt, exp in OFFICIAL:
    got = ascon_encrypt(KEY, NONCE, ad, pt).hex().upper()
    assert got == exp, f"reference model fails KAT Count={count}"
print("reference model matches the KATs")

tests = [(KEY, NONCE, ad, pt) for _, ad, pt, _ in OFFICIAL]
rnd = random.Random(args.seed)
for _ in range(args.n):
    na, npb = rnd.randint(0, 3), rnd.randint(0, 3)
    tests.append((rnd.randbytes(16), rnd.randbytes(16), rnd.randbytes(16 * na), rnd.randbytes(16 * npb)))


def word128(b16):
    return f"{int.from_bytes(b16, 'little'):032x}"


with open(args.out, "w") as f:
    f.write(f"{len(tests)}\n")
    for key, nonce, ad, pt in tests:
        out = ascon_encrypt(key, nonce, ad, pt)
        ct, tag = out[:-16], out[-16:]
        na, npb = len(ad) // 16, len(pt) // 16
        f.write(f"{na} {npb}\n{word128(key)}\n{word128(nonce)}\n")
        for blk in [ad[16*i:16*i+16] for i in range(na)] + [pt[16*i:16*i+16] for i in range(npb)]:
            f.write(word128(blk) + "\n")
        for i in range(npb):
            f.write(word128(ct[16*i:16*i+16]) + "\n")
        f.write(word128(tag) + "\n")
print(f"wrote {len(tests)} tests to {args.out}")
