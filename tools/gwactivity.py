"""Long-run passive log of when the BLE gateway wakes up and takes the bus.

Tests whether gateway activations are self-inflicted or triggered from
outside: with nobody aboard touching the Balmar app, any activation is
caused by something else (a passing phone, ambient BLE traffic, or a
device off the boat).

Two independent activation signals, both established earlier:
  - gateway status byte 0x00 -> 0x08 means the BLE side is active
  - gateway frame rate roughly triples when it takes over the bus,
    which is what squeezes the display off

Writes a CSV row per bucket for later analysis and prints only notable
buckets plus a periodic heartbeat, so a long log stays readable.

    gwactivity.py [seconds] [csv-path]
"""
import collections
import os
import select
import sys
import termios
import time

sys.path.insert(0, "/root/pi-ballmar/src")
from ballmar_reflector import smartlink

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 43200.0
csv_path = sys.argv[2] if len(sys.argv) > 2 else \
    "/root/captures/2026-08-12/gwactivity.csv"

BUCKET = 10.0
HEARTBEAT = 300.0
# Normal idle gateway traffic is ~10 frames/s (poll + reply at 5 Hz).
BURST_FRAMES_PER_BUCKET = 150

fd = os.open("/dev/ttySC0", os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = a[1] = a[3] = 0
a[2] = termios.CREAD | termios.CLOCAL | termios.CS8
a[4] = a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIFLUSH)

parser = smartlink.FrameParser()
parser.MAX_PAYLOAD = 250

csv = open(csv_path, "a")
if csv.tell() == 0:
    csv.write("clock,gateway_frames,status_0x00,status_other,"
              "reg_polls,reg_replies,display_pagereads,active\n")
    csv.flush()

gw = statuses = None
counts = collections.Counter()
t0 = time.time()
next_bucket = t0 + BUCKET
next_heartbeat = t0 + HEARTBEAT
activations = 0
was_active = False

print("gateway activity log, %.0f h -- passive, we transmit NOTHING"
      % (secs / 3600.0), flush=True)
print("leave the Balmar app CLOSED; any activation here is externally "
      "triggered\n", flush=True)

while time.time() - t0 < secs:
    r, _, _ = select.select([fd], [], [], 0.3)
    now = time.time()
    if r:
        for addr, cmd, pl in parser.feed(os.read(fd, 4096)):
            if addr == 0x02:
                counts["gw"] += 1
                if pl:
                    counts["st_00" if pl[0] == 0x00 else "st_other"] += 1
            elif cmd == 0x11:
                counts["reg_reply" if pl else "reg_poll"] += 1
            elif cmd == smartlink.CMD_PAGE:
                counts["pages"] += 1

    if now >= next_bucket:
        next_bucket = now + BUCKET
        clock = time.strftime("%Y-%m-%d %H:%M:%S")
        active = counts["st_other"] > 0 or counts["gw"] > BURST_FRAMES_PER_BUCKET
        csv.write("%s,%d,%d,%d,%d,%d,%d,%d\n"
                  % (clock, counts["gw"], counts["st_00"], counts["st_other"],
                     counts["reg_poll"], counts["reg_reply"], counts["pages"],
                     int(active)))
        csv.flush()
        if active and not was_active:
            activations += 1
            print("%s  *** GATEWAY ACTIVATED *** frames=%d status_non00=%d "
                  "pages=%d  (activation #%d)"
                  % (clock, counts["gw"], counts["st_other"], counts["pages"],
                     activations), flush=True)
        elif was_active and not active:
            print("%s  gateway back to idle" % clock, flush=True)
        was_active = active
        counts.clear()

    if now >= next_heartbeat:
        next_heartbeat = now + HEARTBEAT
        print("%s  [heartbeat: %.1f h elapsed, %d activations so far]"
              % (time.strftime("%H:%M:%S"), (now - t0) / 3600.0, activations),
              flush=True)
