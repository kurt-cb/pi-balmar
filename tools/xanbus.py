"""Xanbus fast-packet reassembler + ground-truth field finder.

Xanbus is J1939-framed like NMEA 2000, and its multi-frame messages use
N2K fast-packet: byte0 = (sequence << 5) | frame_index; on frame 0,
byte1 = total payload length, payload starts at byte 2. Later frames
carry 7 payload bytes each.

Given known meter readings (e.g. 13.9 V, 19 A), scan every reassembled
payload for 16/32-bit little-endian fields matching those values at
common marine scalings, so real fields can be identified by correlation
rather than guesswork.
"""
import collections
import socket
import struct
import sys
import time

iface = sys.argv[1] if len(sys.argv) > 1 else "can0"
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0

CAN_EFF_MASK = 0x1FFFFFFF
CAN_ERR_FLAG = 0x20000000
FMT = "<IB3x8s"

# (label, expected_value, [(scale_name, raw_value), ...])
TARGETS = [
    # AC: load 118 V / 6 A, input 119 V / 7 A, nominal 60 Hz.
    ("AC 118V", [("mV", 118000), ("0.01V", 11800), ("0.1V", 1180)]),
    ("AC 119V", [("mV", 119000), ("0.01V", 11900), ("0.1V", 1190)]),
    ("AC 6A",   [("mA", 6000), ("0.01A", 600), ("0.1A", 60)]),
    ("AC 7A",   [("mA", 7000), ("0.01A", 700), ("0.1A", 70)]),
    ("60 Hz",   [("0.01Hz", 6000), ("0.1Hz", 600), ("Hz", 60)]),
]
TOLERANCE = {"mV": 900, "0.01V": 90, "0.1V": 9, "mA": 400, "0.1A": 4,
             "0.01A": 40, "A": 1, "0.01Hz": 30, "0.1Hz": 3, "Hz": 0}


def decode_id(can_id):
    prio = (can_id >> 26) & 0x7
    dp = (can_id >> 24) & 0x3
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    sa = can_id & 0xFF
    if pf < 240:
        return prio, (dp << 16) | (pf << 8), sa, ps
    return prio, (dp << 16) | (pf << 8) | ps, sa, 255


s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
s.bind((iface,))
s.settimeout(1.0)

partial = {}                      # (pgn, sa, seq) -> [expected_len, bytearray]
complete = {}                     # (pgn, sa) -> latest full payload
seen_payloads = collections.defaultdict(set)
single = {}

t0 = time.time()
print("reassembling %s for %.0fs..." % (iface, secs), flush=True)
while time.time() - t0 < secs:
    try:
        raw = s.recv(16)
    except socket.timeout:
        continue
    can_id, dlc, data = struct.unpack(FMT, raw)
    if can_id & CAN_ERR_FLAG:
        continue
    prio, pgn, sa, dst = decode_id(can_id & CAN_EFF_MASK)
    data = data[:dlc]
    if dlc < 2:
        single[(pgn, sa)] = data
        continue
    seq, frame_idx = data[0] >> 5, data[0] & 0x1F
    key = (pgn, sa, seq)
    if frame_idx == 0:
        partial[key] = [data[1], bytearray(data[2:])]
    elif key in partial:
        partial[key][1] += data[1:]
    else:
        single[(pgn, sa)] = data
        continue
    exp, buf = partial.get(key, (None, None))
    if exp is not None and len(buf) >= exp:
        payload = bytes(buf[:exp])
        complete[(pgn, sa)] = payload
        seen_payloads[(pgn, sa)].add(payload)
        del partial[key]

print("\n=== reassembled multi-frame PGNs ===")
for (pgn, sa), payload in sorted(complete.items()):
    n_variants = len(seen_payloads[(pgn, sa)])
    print("PGN %-7d src 0x%02X  len=%-3d variants=%-3d  %s"
          % (pgn, sa, len(payload), n_variants, payload.hex(" ")))

print("\n=== single-frame PGNs ===")
for (pgn, sa), payload in sorted(single.items()):
    print("PGN %-7d src 0x%02X  %s" % (pgn, sa, payload.hex(" ")))

print("\n=== candidate field matches vs known meter readings ===")
for (pgn, sa), payload in sorted(complete.items()):
    for label, scalings in TARGETS:
        for off in range(len(payload) - 1):
            u16 = int.from_bytes(payload[off:off + 2], "little")
            s16 = int.from_bytes(payload[off:off + 2], "little", signed=True)
            for scale_name, want in scalings:
                tol = TOLERANCE[scale_name]
                for val, kind in ((u16, "u16"), (s16, "s16")):
                    if abs(val - want) <= tol:
                        print("PGN %-7d src 0x%02X off %-3d %s=%-7d "
                              "~ %s as %s (want %d)"
                              % (pgn, sa, off, kind, val, label, scale_name,
                                 want))
                        break
