"""Passive SmartLink bus survey: tally every valid frame by (addr, cmd, len).

Unlike `--dump` (page responses only), this shows ALL commands, so it
reveals what a foreign bus master (e.g. the Balmar BLE gateway) is
sending. Raw bytes are saved for offline re-analysis.
"""
import collections
import os
import select
import sys
import termios
import time

sys.path.insert(0, "/root/pi-ballmar/src")
from ballmar_reflector import smartlink

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
dev = sys.argv[2] if len(sys.argv) > 2 else "/dev/ttySC0"
raw_path = "/tmp/bus-raw.bin"

fd = os.open(dev, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = a[1] = a[3] = 0
a[2] = termios.CREAD | termios.CLOCAL | termios.CS8
a[4] = a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIFLUSH)

parser = smartlink.FrameParser()
raw = bytearray()
tally = collections.Counter()
examples = {}
first_seen = {}

t0 = time.time()
while time.time() - t0 < secs:
    r, _, _ = select.select([fd], [], [], 0.2)
    if not r:
        continue
    data = os.read(fd, 4096)
    raw += data
    for addr, cmd, payload in parser.feed(data):
        key = (addr, cmd, len(payload))
        tally[key] += 1
        examples.setdefault(key, []).append(payload.hex(" "))
        first_seen.setdefault(key, round(time.time() - t0, 3))
os.close(fd)

with open(raw_path, "wb") as f:
    f.write(bytes(raw))

print("raw_bytes=%d bad_bytes=%d frames=%d elapsed=%.1fs -> %s"
      % (len(raw), parser.bad_bytes, sum(tally.values()), time.time() - t0,
         raw_path))
print()
print("%-6s %-6s %-4s %-8s %s" % ("addr", "cmd", "len", "count", "payloads"))
for (addr, cmd, ln), n in sorted(tally.items(), key=lambda kv: -kv[1]):
    uniq = sorted(set(examples[(addr, cmd, ln)]))
    shown = "; ".join(uniq[:4])
    if len(uniq) > 4:
        shown += "  (+%d more)" % (len(uniq) - 4)
    print("0x%02X   0x%02X   %-4d %-8d %s" % (addr, cmd, ln, n, shown))
