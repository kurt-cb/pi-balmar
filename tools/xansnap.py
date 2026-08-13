"""Snapshot Xanbus PGN payloads, and diff two snapshots byte-by-byte.

Used to isolate a setting's field by controlled experiment: snapshot,
change one setting on the panel, snapshot again, diff. Offsets that are
merely noisy (voltage, current, temperature) change in BOTH snapshots'
internal variants, so they are reported separately from offsets that
moved only between snapshots — the latter are the real candidates.

    xansnap.py capture <out.json> <secs>
    xansnap.py diff <before.json> <after.json>
"""
import json
import socket
import struct
import sys
import time

CAN_EFF_MASK = 0x1FFFFFFF
CAN_ERR_FLAG = 0x20000000
FMT = "<IB3x8s"


def decode_id(can_id):
    dp = (can_id >> 24) & 0x3
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    sa = can_id & 0xFF
    if pf < 240:
        return (dp << 16) | (pf << 8), sa
    return (dp << 16) | (pf << 8) | ps, sa


def capture(path, secs, iface="can0"):
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((iface,))
    s.settimeout(1.0)
    partial, variants = {}, {}
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            raw = s.recv(16)
        except socket.timeout:
            continue
        can_id, dlc, data = struct.unpack(FMT, raw)
        if can_id & CAN_ERR_FLAG:
            continue
        pgn, sa = decode_id(can_id & CAN_EFF_MASK)
        data = data[:dlc]
        if dlc < 2:
            continue
        seq, idx = data[0] >> 5, data[0] & 0x1F
        key = (pgn, sa, seq)
        if idx == 0:
            partial[key] = [data[1], bytearray(data[2:])]
            continue
        if key not in partial:
            continue
        partial[key][1] += data[1:]
        exp, buf = partial[key]
        if len(buf) < exp:
            continue
        del partial[key]
        variants.setdefault("%d/%d" % (pgn, sa), set()).add(bytes(buf[:exp]).hex())
    out = {k: sorted(v) for k, v in variants.items()}
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print("captured %d PGNs -> %s" % (len(out), path))
    for k, v in sorted(out.items()):
        print("  %-12s variants=%-3d %s" % (k, len(v), v[0]))


def diff(a_path, b_path):
    a = json.load(open(a_path))
    b = json.load(open(b_path))
    for key in sorted(set(a) | set(b)):
        if key not in a or key not in b:
            print("%-12s present in only one snapshot" % key)
            continue
        av, bv = [bytes.fromhex(x) for x in a[key]], [bytes.fromhex(x) for x in b[key]]
        n = min(min(len(x) for x in av), min(len(x) for x in bv))
        noisy, changed = [], []
        for off in range(n):
            a_set = {x[off] for x in av}
            b_set = {x[off] for x in bv}
            if len(a_set) > 1 or len(b_set) > 1:
                noisy.append(off)
            elif a_set != b_set:
                changed.append((off, a_set.pop(), b_set.pop()))
        if not changed:
            continue
        print("\n=== %s ===" % key)
        print("  stable offsets that CHANGED between snapshots:")
        for off, x, y in changed:
            print("    off %-3d  0x%02X -> 0x%02X   (%d -> %d)" % (off, x, y, x, y))
        # Report 16/32-bit little-endian reads spanning the changed offsets.
        for off, _, _ in changed:
            lo = max(0, off - 3)
            for start in range(lo, off + 1):
                for width in (2, 4):
                    if start + width > n or not (start <= off < start + width):
                        continue
                    av0 = int.from_bytes(av[0][start:start + width], "little")
                    bv0 = int.from_bytes(bv[0][start:start + width], "little")
                    if av0 != bv0:
                        print("    u%d @%-3d %8d -> %-8d  (/1000: %.3f -> %.3f)"
                              % (width * 8, start, av0, bv0, av0 / 1000.0,
                                 bv0 / 1000.0))
        if noisy:
            print("  (noisy offsets ignored: %s)"
                  % ", ".join(str(o) for o in noisy))


if sys.argv[1] == "capture":
    capture(sys.argv[2], float(sys.argv[3]))
else:
    diff(sys.argv[2], sys.argv[3])
