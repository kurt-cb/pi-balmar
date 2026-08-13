"""Live Xanbus DC monitor — decodes PGN 127172/127173 as V/I.

Hypothesis under test (from correlating against a known 13.9 V / 19 A
meter reading):
    offset 2, u32 LE = voltage in mV
    offset 6, s32 LE = current in mA (signed: charge positive)

Prints a line per update so the values can be watched against the panel
display. If the decode is right, these track the real readings live.
"""
import socket
import struct
import sys
import time

iface = sys.argv[1] if len(sys.argv) > 1 else "can0"
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0

CAN_EFF_MASK = 0x1FFFFFFF
CAN_ERR_FLAG = 0x20000000
FMT = "<IB3x8s"
WATCH = {127172: "127172", 127173: "127173", 126990: "126990(cfg)"}


def decode_id(can_id):
    dp = (can_id >> 24) & 0x3
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    sa = can_id & 0xFF
    if pf < 240:
        return (dp << 16) | (pf << 8), sa
    return (dp << 16) | (pf << 8) | ps, sa


s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
s.bind((iface,))
s.settimeout(1.0)

partial = {}
last = {}
t0 = time.time()
print("%-8s %-12s %-10s %-10s %s" % ("t(s)", "PGN", "volts", "amps", "watts"),
      flush=True)
while time.time() - t0 < secs:
    try:
        raw = s.recv(16)
    except socket.timeout:
        continue
    can_id, dlc, data = struct.unpack(FMT, raw)
    if can_id & CAN_ERR_FLAG:
        continue
    pgn, sa = decode_id(can_id & CAN_EFF_MASK)
    if pgn not in WATCH or dlc < 2:
        continue
    data = data[:dlc]
    seq, idx = data[0] >> 5, data[0] & 0x1F
    key = (pgn, sa, seq)
    if idx == 0:
        partial[key] = [data[1], bytearray(data[2:])]
        continue
    if key not in partial:
        continue
    partial[key][1] += data[1:]
    exp, buf = partial[key]
    if len(buf) < exp or len(buf) < 10:
        continue
    payload = bytes(buf[:exp])
    del partial[key]
    volts = int.from_bytes(payload[2:6], "little") / 1000.0
    amps = int.from_bytes(payload[6:10], "little", signed=True) / 1000.0
    if last.get((pgn, sa)) == (volts, amps):
        continue
    last[(pgn, sa)] = (volts, amps)
    print("%-8.1f %-12s %-10.3f %-10.3f %.1f"
          % (time.time() - t0, WATCH[pgn], volts, amps, volts * amps),
          flush=True)
