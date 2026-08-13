"""Passive per-second SmartLink health recorder, for syncing to an event.

Transmits NOTHING, so whatever happens on the bus is entirely the doing
of the master, the gateway, or the BLE app -- never us.

Emits one line per second with wall-clock time and each regulator's
reply rate, so an external event (pressing "scan" in the BLE app) can be
lined up against the exact moment a regulator stops answering. Any new
frame type or gateway status change prints immediately.
"""
import collections
import os
import select
import sys
import termios
import time

sys.path.insert(0, "/root/pi-ballmar/src")
from ballmar_reflector import smartlink

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 900.0
REGS = (0x81, 0x82)

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
gw_status = set()
seen = set()
alive = {addr: None for addr in REGS}
t0 = time.time()
next_tick = t0 + 1.0

print("passive SmartLink watch, %.0fs -- we transmit NOTHING" % secs,
      flush=True)
print("press BLE scan whenever ready; the second it lands is recorded\n",
      flush=True)
print("%-10s %-22s %s" % ("clock", "0x81 (STBD/house)", "0x82 (PORT/start)"),
      flush=True)

while time.time() - t0 < secs:
    r, _, _ = select.select([fd], [], [], 0.2)
    now = time.time()
    if r:
        for addr, cmd, pl in parser.feed(os.read(fd, 4096)):
            if addr in REGS and cmd == 0x11:
                (replies if pl else polls)[addr] += 1
            if addr == 0x02 and cmd == 0x00 and pl:
                if pl[0] not in gw_status:
                    gw_status.add(pl[0])
                    print("%-10s GATEWAY status byte -> 0x%02X"
                          % (time.strftime("%H:%M:%S"), pl[0]), flush=True)
            key = (addr, cmd, len(pl))
            if key not in seen:
                seen.add(key)
                print("%-10s NEW frame addr=0x%02X cmd=0x%02X len=%d %s"
                      % (time.strftime("%H:%M:%S"), addr, cmd, len(pl),
                         pl.hex(" ") or "-"), flush=True)

    if now >= next_tick:
        next_tick = now + 1.0
        cells = []
        for addr in REGS:
            p, q = polls[addr], replies[addr]
            rate = (100.0 * q / p) if p else 0.0
            state = "UP" if q else ("DOWN" if p else "-")
            if alive[addr] is not None and (q > 0) != alive[addr]:
                print("%-10s *** 0x%02X went %s ***"
                      % (time.strftime("%H:%M:%S"), addr,
                         "UP" if q else "DOWN"), flush=True)
            alive[addr] = q > 0
            cells.append("%-4s %3d/%-3d %5.1f%%" % (state, q, p, rate))
        print("%-10s %-22s %s"
              % (time.strftime("%H:%M:%S"), cells[0], cells[1]), flush=True)
        polls.clear()
        replies.clear()
