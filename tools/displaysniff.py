"""Passive capture of what the Balmar display asks for.

With a display on the bus, page reads (cmd 0x15) that we previously had
to send ourselves may appear for free -- and every value the display
requests is one it is about to show on screen, so its reading can be
matched to the raw value without ever transmitting.

Keepalive chatter (cmd 0x11) and gateway status polls are counted but
not printed, so anything genuinely new stands out.

    displaysniff.py [seconds] [out-prefix]
"""
import collections
import json
import os
import select
import sys
import termios
import time

sys.path.insert(0, "/root/pi-ballmar/src")
from ballmar_reflector import smartlink

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 3600.0
out = sys.argv[2] if len(sys.argv) > 2 else "/root/captures/2026-08-12/display"

fd = os.open("/dev/ttySC0", os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = a[1] = a[3] = 0
a[2] = termios.CREAD | termios.CLOCAL | termios.CS8
a[4] = a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIFLUSH)

parser = smartlink.FrameParser()
parser.MAX_PAYLOAD = 250

raw_f = open(out + ".bin", "wb")
jsonl_f = open(out + ".jsonl", "w")
noise = collections.Counter()
seen = set()
pages = {}
t0 = time.time()
next_tick = t0 + 15.0

print("passive display sniff, %.0fs -- we transmit NOTHING" % secs, flush=True)
print("cycle the display through its screens; page reads will appear here\n",
      flush=True)

while time.time() - t0 < secs:
    r, _, _ = select.select([fd], [], [], 0.3)
    now = time.time()
    if r:
        data = os.read(fd, 4096)
        raw_f.write(data)
        raw_f.flush()
        for addr, cmd, pl in parser.feed(data):
            t = now - t0
            jsonl_f.write(json.dumps({"t": round(t, 3), "addr": addr,
                                      "cmd": cmd, "payload": pl.hex()}) + "\n")
            # Routine chatter: count, don't print.
            if cmd == 0x11 or (addr == 0x02 and cmd == 0x00):
                noise[(addr, cmd, len(pl))] += 1
                continue
            jsonl_f.flush()
            if cmd == smartlink.CMD_PAGE and len(pl) >= 5:
                page = pl[0]
                value = int.from_bytes(pl[1:5], "big", signed=True)
                prev = pages.get((addr, page))
                pages[(addr, page)] = value
                trend = "" if prev is None or prev == value \
                    else "  (was %d)" % prev
                print("%s  PAGE  addr=0x%02X page=0x%02X  value=%-12d%s"
                      % (time.strftime("%H:%M:%S"), addr, page, value, trend),
                      flush=True)
                continue
            key = (addr, cmd, len(pl))
            if key not in seen:
                seen.add(key)
                print("%s  NEW   addr=0x%02X cmd=0x%02X len=%-3d %s"
                      % (time.strftime("%H:%M:%S"), addr, cmd, len(pl),
                         pl.hex(" ") or "-"), flush=True)

    if now >= next_tick:
        next_tick = now + 15.0
        print("%s  [chatter: %s | distinct pages seen: %d]"
              % (time.strftime("%H:%M:%S"),
                 ", ".join("0x%02X/0x%02X x%d" % (a_, c, n)
                           for (a_, c, _), n in sorted(noise.items())) or "-",
                 len(pages)), flush=True)
        noise.clear()
