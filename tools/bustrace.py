"""Chronological SmartLink frame trace with inter-frame gaps.

Gaps reveal request/response pairing on the half-duplex wire: a response
follows its request by ~1-3 ms, while a master's next poll comes after a
much longer idle. Frames are timestamped as they are parsed.
"""
import os
import select
import sys
import termios
import time

sys.path.insert(0, "/root/pi-ballmar/src")
from ballmar_reflector import smartlink

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
dev = sys.argv[2] if len(sys.argv) > 2 else "/dev/ttySC0"

fd = os.open(dev, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = a[1] = a[3] = 0
a[2] = termios.CREAD | termios.CLOCAL | termios.CS8
a[4] = a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIFLUSH)

parser = smartlink.FrameParser()
events = []
t0 = time.time()
while time.time() - t0 < secs:
    r, _, _ = select.select([fd], [], [], 0.2)
    if not r:
        continue
    data = os.read(fd, 4096)
    now = time.time() - t0
    for addr, cmd, payload in parser.feed(data):
        events.append((now, addr, cmd, payload))
os.close(fd)

print("%-9s %-7s %-6s %-6s %-4s %s"
      % ("t(s)", "gap(ms)", "addr", "cmd", "len", "payload"))
prev = None
for t, addr, cmd, payload in events:
    gap = "" if prev is None else "%.1f" % ((t - prev) * 1000)
    kind = {0x00: "status", 0x04: "ident", 0x15: "page"}.get(cmd, "?")
    print("%-9.4f %-7s 0x%02X   0x%02X   %-4d %-18s %s"
          % (t, gap, addr, cmd, len(payload), payload.hex(" ") or "-", kind))
    prev = t
print("\nframes=%d bad_bytes=%d" % (len(events), parser.bad_bytes))
