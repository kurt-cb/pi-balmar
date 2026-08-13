"""Watch the Xanbus charge-rate candidate and the measured current together.

Records the transition when a setting is changed on the panel, so cause
and effect land in one trace:

  PGN 75264  off 39, u16 LE, 0.01 A  -- candidate charge-rate setpoint
  PGN 127172 off 2 u32 mV / off 6 s32 mA -- measured voltage and current

Setpoint changes print immediately; current prints on a fixed cadence so
the trend before and after the change is visible.
"""
import socket
import struct
import sys
import time

iface = sys.argv[1] if len(sys.argv) > 1 else "can0"
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
PERIOD = 2.0

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


s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
s.bind((iface,))
s.settimeout(1.0)

partial = {}
setpoint = None
volts = amps = None
samples = []
next_print = time.time() + PERIOD
t0 = time.time()
print("watching %s for %.0fs — change the setting now" % (iface, secs),
      flush=True)
print("%-8s %-10s %s" % ("t(s)", "event", "detail"), flush=True)

while time.time() - t0 < secs:
    try:
        raw = s.recv(16)
    except socket.timeout:
        raw = None
    now = time.time()
    if raw:
        can_id, dlc, data = struct.unpack(FMT, raw)
        if not (can_id & CAN_ERR_FLAG) and dlc >= 2:
            pgn, sa = decode_id(can_id & CAN_EFF_MASK)
            data = data[:dlc]
            seq, idx = data[0] >> 5, data[0] & 0x1F
            key = (pgn, sa, seq)
            if idx == 0:
                partial[key] = [data[1], bytearray(data[2:])]
            elif key in partial:
                partial[key][1] += data[1:]
                exp, buf = partial[key]
                if len(buf) >= exp:
                    payload = bytes(buf[:exp])
                    del partial[key]
                    if pgn == 75264 and len(payload) >= 41:
                        sp = int.from_bytes(payload[39:41], "little") / 100.0
                        if sp != setpoint:
                            print("%-8.1f %-10s %.2f A%s"
                                  % (now - t0, "SETPOINT", sp,
                                     "" if setpoint is None
                                     else "  (was %.2f A)" % setpoint),
                                  flush=True)
                            setpoint = sp
                    elif pgn == 127172 and len(payload) >= 10:
                        volts = int.from_bytes(payload[2:6], "little") / 1000.0
                        amps = int.from_bytes(payload[6:10], "little",
                                              signed=True) / 1000.0
                        samples.append(amps)
    if now >= next_print:
        next_print = now + PERIOD
        if samples:
            avg = sum(samples) / len(samples)
            print("%-8.1f %-10s %.2f V  %.2f A avg (min %.2f, max %.2f, n=%d)"
                  % (now - t0, "measured", volts, avg, min(samples),
                     max(samples), len(samples)), flush=True)
            samples = []
