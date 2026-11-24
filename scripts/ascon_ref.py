# Ascon-AEAD128 encryption only, transcribed from the reference implementation
# pyascon (https://github.com/meichlseder/pyascon, CC0).

debug = False
debugpermutation = False

def ascon_encrypt(key, nonce, associateddata, plaintext, variant="Ascon-AEAD128"):
    versions = {"Ascon-AEAD128": 1}
    assert variant in versions.keys()
    assert len(key) == 16 and len(nonce) == 16
    S = [0, 0, 0, 0, 0]
    k = len(key) * 8
    a = 12
    b = 8
    rate = 16
    ascon_initialize(S, k, rate, a, b, versions[variant], key, nonce)
    ascon_process_associated_data(S, b, rate, associateddata)
    ciphertext = ascon_process_plaintext(S, b, rate, plaintext)
    tag = ascon_finalize(S, rate, a, key)
    return ciphertext + tag

def ascon_initialize(S, k, rate, a, b, version, key, nonce):
    taglen = 128
    iv = to_bytes([version, 0, (b<<4) + a]) + int_to_bytes(taglen, 2) + to_bytes([rate, 0, 0])
    S[0], S[1], S[2], S[3], S[4] = bytes_to_state(iv + key + nonce)
    ascon_permutation(S, a)
    zero_key = bytes_to_state(zero_bytes(40-len(key)) + key)
    for i in range(5):
        S[i] ^= zero_key[i]

def ascon_process_associated_data(S, b, rate, associateddata):
    if len(associateddata) > 0:
        a_padding = to_bytes([0x01]) + zero_bytes(rate - (len(associateddata) % rate) - 1)
        a_padded = to_bytes(associateddata) + a_padding
        for block in range(0, len(a_padded), rate):
            S[0] ^= bytes_to_int(a_padded[block:block+8])
            if rate == 16:
                S[1] ^= bytes_to_int(a_padded[block+8:block+16])
            ascon_permutation(S, b)
    S[4] ^= 1<<63

def ascon_process_plaintext(S, b, rate, plaintext):
    p_lastlen = len(plaintext) % rate
    p_padding = to_bytes([0x01]) + zero_bytes(rate-p_lastlen-1)
    p_padded = to_bytes(plaintext) + p_padding
    ciphertext = to_bytes([])
    for block in range(0, len(p_padded) - rate, rate):
        S[0] ^= bytes_to_int(p_padded[block:block+8])
        S[1] ^= bytes_to_int(p_padded[block+8:block+16])
        ciphertext += (int_to_bytes(S[0], 8) + int_to_bytes(S[1], 8))
        ascon_permutation(S, b)
    block = len(p_padded) - rate
    S[0] ^= bytes_to_int(p_padded[block:block+8])
    S[1] ^= bytes_to_int(p_padded[block+8:block+16])
    ciphertext += (int_to_bytes(S[0], 8)[:min(8,p_lastlen)] + int_to_bytes(S[1], 8)[:max(0,p_lastlen-8)])
    return ciphertext

def ascon_finalize(S, rate, a, key):
    S[rate//8+0] ^= bytes_to_int(key[0:8])
    S[rate//8+1] ^= bytes_to_int(key[8:16])
    ascon_permutation(S, a)
    S[3] ^= bytes_to_int(key[-16:-8])
    S[4] ^= bytes_to_int(key[-8:])
    return int_to_bytes(S[3], 8) + int_to_bytes(S[4], 8)

def ascon_permutation(S, rounds=1):
    assert rounds <= 12
    for r in range(12-rounds, 12):
        S[2] ^= (0xf0 - r*0x10 + r*0x1)
        S[0] ^= S[4]
        S[4] ^= S[3]
        S[2] ^= S[1]
        T = [(S[i] ^ 0xFFFFFFFFFFFFFFFF) & S[(i+1)%5] for i in range(5)]
        for i in range(5):
            S[i] ^= T[(i+1)%5]
        S[1] ^= S[0]
        S[0] ^= S[4]
        S[3] ^= S[2]
        S[2] ^= 0XFFFFFFFFFFFFFFFF
        S[0] ^= rotr(S[0], 19) ^ rotr(S[0], 28)
        S[1] ^= rotr(S[1], 61) ^ rotr(S[1], 39)
        S[2] ^= rotr(S[2], 1) ^ rotr(S[2], 6)
        S[3] ^= rotr(S[3], 10) ^ rotr(S[3], 17)
        S[4] ^= rotr(S[4], 7) ^ rotr(S[4], 41)

def zero_bytes(n): return n * b"\x00"
def to_bytes(l): return bytes(l)
def bytes_to_int(b): return int.from_bytes(b, 'little')
def bytes_to_state(b): return [bytes_to_int(b[8*w:8*(w+1)]) for w in range(5)]
def int_to_bytes(integer, nbytes): return integer.to_bytes(nbytes, 'little')
def rotr(val, r): return (val >> r) | ((val & (1<<r)-1) << (64-r))
