"""Watch SmartLink (RS-485) and Xanbus (CAN) on one timeline.

With no Balmar display aboard, the Xantrex is the instrument: the house
MC-618 charges the same bank the SW3000 measures, so when its field
enables, Xanbus PGN 127172 shows voltage rising and current shifting.
Putting both buses in one timestamped trace lets an MC-618 payload be
decoded against a known DC reading -- the same correlation trick that
worked for Xanbus.

Single process on purpose: two readers on the same tty would split the
byte stream between them and corrupt both.

Outputs:
  <out>.jsonl  every SmartLink frame {t, addr, cmd, payload}
  <out>.bin    raw SmartLink bytes (nothing lost to framing assumptions)
  stdout       regulator wake-ups, new frame types, and DC readings
"""
import json
import os
import select
import socket
import struct
import sys
import termios
import time

sys.path.insert(0, "/root/pi-ballmar/src")
from ballmar_reflector import smartlink

out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dualwatch"
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 5400.0
dev = "/dev/ttySC0"
REGULATORS = (0x81, 0x82)
DC_PERIOD = 5.0
DC_JUMP_V = 0.25
DC_JUMP_A = 4.0

CAN_EFF_MASK = 0x1FFFFFFF
CAN_ERR_FLAG = 0x20000000
CAN_FMT = "<IB3x8s"

fd = os.open(dev, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = a[1] = a[3] = 0
a[2] = termios.CREAD | termios.CLOCAL | termios.CS8
a[4] = a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIFLUSH)

can = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
can.bind(("can0",))
can.setblocking(False)

parser = smartlink.FrameParser()
# A regulator's reply may exceed the SG200-derived 32-byte cap; raising it
# on this instance keeps a large frame from being discarded as noise.
parser.MAX_PAYLOAD = 250

raw_f = open(out + ".bin", "wb")
jsonl_f = open(out + ".jsonl", "w")
seen = set()
partial = {}
last_dc = None
next_dc = 0.0
t0 = time.time()


def stamp(t):
    return "%s (+%6.1fs)" % (time.strftime("%H:%M:%S"), t)


print("dual-bus watch: %s + can0 for %.0fs -> %s.{jsonl,bin}"
      % (dev, secs, out), flush=True)
print("%-22s %s" % ("time", "event"), flush=True)

while time.time() - t0 < secs:
    r, _, _ = select.select([fd, can], [], [], 1.0)
    now = time.time()
    t = now - t0

    if fd in r:
        data = os.read(fd, 4096)
        raw_f.write(data)
        raw_f.flush()
        for addr, cmd, payload in parser.feed(data):
            jsonl_f.write(json.dumps({"t": round(t, 4), "addr": addr,
                                      "cmd": cmd,
                                      "payload": payload.hex()}) + "\n")
            key = (addr, cmd, len(payload))
            if key not in seen:
                seen.add(key)
                jsonl_f.flush()
                tag = "REGULATOR" if addr in REGULATORS and payload else "new"
                print("%-22s %-9s addr=0x%02X cmd=0x%02X len=%-3d %s"
                      % (stamp(t), tag, addr, cmd, len(payload),
                         payload.hex(" ") or "-"), flush=True)
            elif addr in REGULATORS and payload:
                print("%-22s %-9s addr=0x%02X cmd=0x%02X %s"
                      % (stamp(t), "REG-DATA", addr, cmd, payload.hex(" ")),
                      flush=True)

    if can in r:
        try:
            while True:
                frame = can.recv(16)
                can_id, dlc, cdata = struct.unpack(CAN_FMT, frame)
                if (can_id & CAN_ERR_FLAG) or dlc < 2:
                    continue
                cid = can_id & CAN_EFF_MASK
                pf, ps = (cid >> 16) & 0xFF, (cid >> 8) & 0xFF
                dp, sa = (cid >> 24) & 0x3, cid & 0xFF
                pgn = ((dp << 16) | (pf << 8) | ps) if pf >= 240 else \
                      ((dp << 16) | (pf << 8))
                if pgn != 127172:
                    continue
                cdata = cdata[:dlc]
                seq, idx = cdata[0] >> 5, cdata[0] & 0x1F
                k = (sa, seq)
                if idx == 0:
                    partial[k] = [cdata[1], bytearray(cdata[2:])]
                elif k in partial:
                    partial[k][1] += cdata[1:]
                    exp, buf = partial[k]
                    if len(buf) >= exp and len(buf) >= 10:
                        p = bytes(buf[:exp])
                        del partial[k]
                        v = int.from_bytes(p[2:6], "little") / 1000.0
                        i = int.from_bytes(p[6:10], "little", signed=True) / 1000.0
                        w = int.from_bytes(p[10:14], "little")
                        jump = last_dc and (abs(v - last_dc[0]) >= DC_JUMP_V or
                                            abs(i - last_dc[1]) >= DC_JUMP_A)
                        if jump or now >= next_dc:
                            print("%-22s %-9s %.3f V  %+.3f A  %d W%s"
                                  % (stamp(t), "DC-JUMP" if jump else "dc",
                                     v, i, w, "   <<<" if jump else ""),
                                  flush=True)
                            next_dc = now + DC_PERIOD
                            last_dc = (v, i)
        except BlockingIOError:
            pass

raw_f.close()
jsonl_f.close()
print("done: smartlink_combos=%d bad_bytes=%d" % (len(seen), parser.bad_bytes),
      flush=True)
