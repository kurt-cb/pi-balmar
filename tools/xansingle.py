"""Collect the set of distinct raw frame payloads per (PGN, source).

Covers the single-frame PGNs that the fast-packet snapshotter skips —
where small enum/state fields (inverter on/off, charge stage) live.

Reads either a live CAN interface or a candump -l log file, so an
earlier saved capture can serve as the "before" side of a comparison:

    xansingle.py live <out.json> <secs>
    xansingle.py log  <candump.log> <out.json>
    xansingle.py diff <before.json> <after.json>
"""
import json
import re
import socket
import struct
import sys
import time

CAN_EFF_MASK = 0x1FFFFFFF
CAN_ERR_FLAG = 0x20000000
FMT = "<IB3x8s"
LOG_RE = re.compile(r"\(([\d.]+)\)\s+(\S+)\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)")


def decode_id(can_id):
    dp = (can_id >> 24) & 0x3
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    sa = can_id & 0xFF
    if pf < 240:
        return (dp << 16) | (pf << 8), sa
    return (dp << 16) | (pf << 8) | ps, sa


def record(store, pgn, sa, payload):
    store.setdefault("%d/%d" % (pgn, sa), set()).add(payload.hex())


def save(store, path):
    out = {k: sorted(v) for k, v in store.items()}
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print("saved %d (PGN,src) groups -> %s" % (len(out), path))
    for k, v in sorted(out.items(), key=lambda kv: len(kv[1])):
        if len(v) <= 6:
            print("  %-12s %d distinct: %s" % (k, len(v), ", ".join(v)))
        else:
            print("  %-12s %d distinct (noisy)" % (k, len(v)))


def from_log(path, out):
    store = {}
    with open(path) as f:
        for line in f:
            m = LOG_RE.search(line)
            if not m:
                continue
            can_id = int(m.group(3), 16)
            if can_id & CAN_ERR_FLAG:
                continue
            pgn, sa = decode_id(can_id & CAN_EFF_MASK)
            record(store, pgn, sa, bytes.fromhex(m.group(4)))
    save(store, out)


def from_live(out, secs, iface="can0"):
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((iface,))
    s.settimeout(1.0)
    store = {}
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
        record(store, pgn, sa, data[:dlc])
    save(store, out)


def diff(a_path, b_path, max_variants=6):
    a, b = json.load(open(a_path)), json.load(open(b_path))
    print("=== groups that are stable in BOTH but differ ===")
    for key in sorted(set(a) & set(b)):
        av, bv = set(a[key]), set(b[key])
        if len(av) > max_variants or len(bv) > max_variants or av == bv:
            continue
        print("\n%s" % key)
        print("  before: %s" % ", ".join(sorted(av)))
        print("  after : %s" % ", ".join(sorted(bv)))
        gone, new = sorted(av - bv), sorted(bv - av)
        for x in gone:
            for y in new:
                if len(x) != len(y):
                    continue
                xb, yb = bytes.fromhex(x), bytes.fromhex(y)
                d = [(i, p, q) for i, (p, q) in enumerate(zip(xb, yb)) if p != q]
                if d and len(d) <= 4:
                    print("    %s -> %s at %s" % (x, y, ", ".join(
                        "off %d: 0x%02X->0x%02X" % t for t in d)))
    only_b = sorted(set(b) - set(a))
    if only_b:
        print("\n=== present only AFTER: %s ===" % ", ".join(only_b))


cmd = sys.argv[1]
if cmd == "live":
    from_live(sys.argv[2], float(sys.argv[3]))
elif cmd == "log":
    from_log(sys.argv[2], sys.argv[3])
else:
    diff(sys.argv[2], sys.argv[3])
