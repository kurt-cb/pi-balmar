"""Long-running passive SmartLink logger, armed for MC-618 wake-up.

Writes two files so nothing is lost if the MC-618 reply violates our
current framing assumptions (FrameParser caps payloads at 32 bytes, and
a bulk regulator block could be larger):

  <out>.bin    every raw byte, with a timestamp index
  <out>.jsonl  every checksum-valid frame: {t, addr, cmd, payload}

Prints a line to stdout the first time each (addr, cmd, len) combination
appears, so a regulator coming alive is visible immediately.

Read-only: never transmits, so it cannot collide with the bus master.
"""
import json
import os
import select
import sys
import termios
import time

sys.path.insert(0, "/root/pi-ballmar/src")
from ballmar_reflector import smartlink

out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/buslog"
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 86400.0
dev = sys.argv[3] if len(sys.argv) > 3 else "/dev/ttySC0"

fd = os.open(dev, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = a[1] = a[3] = 0
a[2] = termios.CREAD | termios.CLOCAL | termios.CS8
a[4] = a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIFLUSH)

parser = smartlink.FrameParser()
seen = set()
n_frames = 0
t0 = time.time()

raw_f = open(out + ".bin", "wb")
jsonl_f = open(out + ".jsonl", "w")
idx_f = open(out + ".idx", "w")
print("logging %s -> %s.{bin,jsonl,idx} for %.0fs (read-only)"
      % (dev, out, secs), flush=True)

try:
    while time.time() - t0 < secs:
        r, _, _ = select.select([fd], [], [], 0.5)
        if not r:
            continue
        data = os.read(fd, 4096)
        t = round(time.time() - t0, 4)
        pos = raw_f.tell()
        raw_f.write(data)
        raw_f.flush()
        idx_f.write("%s %d %d\n" % (t, pos, len(data)))
        idx_f.flush()
        for addr, cmd, payload in parser.feed(data):
            n_frames += 1
            jsonl_f.write(json.dumps({"t": t, "addr": addr, "cmd": cmd,
                                      "payload": payload.hex()}) + "\n")
            key = (addr, cmd, len(payload))
            if key not in seen:
                seen.add(key)
                jsonl_f.flush()
                print("[%8.2fs] NEW  addr=0x%02X cmd=0x%02X len=%-3d %s"
                      % (t, addr, cmd, len(payload), payload.hex(" ") or "-"),
                      flush=True)
finally:
    raw_f.close()
    jsonl_f.close()
    idx_f.close()
    print("stopped: frames=%d bad_bytes=%d combos=%d elapsed=%.0fs"
          % (n_frames, parser.bad_bytes, len(seen), time.time() - t0),
          flush=True)
