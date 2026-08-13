"""Passive SmartLink watch focused on the gateway coming and going.

For unplug/replug testing of the Balmar NMEA-2000/BLE gateway (addr
0x02): logs once per second with wall-clock time so the moment it drops
and rejoins is pinned down, and prints every status-byte value change
and any new frame type (e.g. an identity exchange on reconnect).

Transmits NOTHING, so it cannot interfere with the gateway rejoining or
with anything else on the bus.

    gwwatch.py [seconds] [logfile]
"""
import collections
import os
import select
import sys
import termios
import time

sys.path.insert(0, "/root/pi-ballmar/src")
from ballmar_reflector import smartlink

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 7200.0
GATEWAY = 0x02

fd = os.open("/dev/ttySC0", os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = a[1] = a[3] = 0
a[2] = termios.CREAD | termios.CLOCAL | termios.CS8
a[4] = a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIFLUSH)

parser = smartlink.FrameParser()
parser.MAX_PAYLOAD = 250

polls = collections.Counter()
replies = collections.Counter()
statuses = collections.Counter()
seen = set()
present = None
t0 = time.time()
next_tick = t0 + 1.0

print("passive gateway watch, %.0fs -- we transmit NOTHING" % secs, flush=True)
print("unplug / replug the NMEA 2000 device whenever ready\n", flush=True)

while time.time() - t0 < secs:
    r, _, _ = select.select([fd], [], [], 0.2)
    now = time.time()
    if r:
        for addr, cmd, pl in parser.feed(os.read(fd, 4096)):
            if addr == GATEWAY:
                (replies if pl else polls)[cmd] += 1
                if pl:
                    statuses[pl[0]] += 1
            key = (addr, cmd, len(pl), pl.hex())
            if key not in seen:
                seen.add(key)
                print("%s  NEW  addr=0x%02X cmd=0x%02X len=%d  %s"
                      % (time.strftime("%H:%M:%S"), addr, cmd, len(pl),
                         pl.hex(" ") or "-"), flush=True)

    if now >= next_tick:
        next_tick = now + 1.0
        p = sum(polls.values())
        q = sum(replies.values())
        here = q > 0
        if present is not None and here != present:
            print("%s  *** GATEWAY %s ***"
                  % (time.strftime("%H:%M:%S"),
                     "BACK ONLINE" if here else "GONE (no replies)"),
                  flush=True)
        present = here
        st = ", ".join("0x%02X x%d" % (k, v)
                       for k, v in sorted(statuses.items())) or "-"
        print("%s  gateway %-7s replies=%-3d polls=%-3d status: %s"
              % (time.strftime("%H:%M:%S"), "UP" if here else "DOWN", q, p, st),
              flush=True)
        polls.clear()
        replies.clear()
        statuses.clear()
