"""Probe the MC-618 regulators for readable pages.

The bus master only ever sends cmd 0x11 keepalives to 0x81/0x82, so
regulator data never appears passively -- it has to be requested.

Safety boundary: this sends ONLY known READ operations --
  cmd 0x04 (identity)  and  cmd 0x15 (page read, payload = page code).
It deliberately does not sweep arbitrary command codes: an unknown
command to a device driving alternator field could be a write.

Bench timing applies (docs/rs-485 decode.md): >=10 ms of quiet between
consecutive TX frames, or frames are ignored entirely.
"""
import os
import select
import sys
import termios
import time

sys.path.insert(0, "/root/pi-ballmar/src")
from ballmar_reflector import smartlink

dev = "/dev/ttySC0"
ADDRS = [int(x, 0) for x in (sys.argv[1].split(",") if len(sys.argv) > 1
                             else ["0x81", "0x82"])]
TX_GAP = 0.014
WINDOW = 0.030

fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = a[1] = a[3] = 0
a[2] = termios.CREAD | termios.CLOCAL | termios.CS8
a[4] = a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIOFLUSH)

parser = smartlink.FrameParser()
parser.MAX_PAYLOAD = 250


def collect(seconds):
    out = []
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.005)
        if r:
            out += parser.feed(os.read(fd, 4096))
    return out


def ask(frame, window=WINDOW):
    os.write(fd, frame)
    frames = collect(window)
    time.sleep(TX_GAP)
    return frames


for addr in ADDRS:
    print("\n=== 0x%02X identity (cmd 0x04) ===" % addr, flush=True)
    for a_, c_, pl in ask(smartlink.build_frame(addr, smartlink.CMD_IDENT), 0.09):
        if a_ == addr and c_ == smartlink.CMD_IDENT and len(pl) > 1:
            header, name = smartlink.parse_identity(pl)
            print("  name=%r header=%s raw=%s" % (name, header, pl.hex(" ")))

    print("=== 0x%02X page sweep (cmd 0x15, pages 0x00-0xFF) ===" % addr,
          flush=True)
    hits = {}
    for page in range(256):
        for a_, c_, pl in ask(smartlink.build_page_request(page, addr)):
            if a_ != addr or c_ != smartlink.CMD_PAGE or len(pl) < 2:
                continue
            got = pl[0]
            value = int.from_bytes(pl[1:5], "big", signed=True) if len(pl) >= 5 else None
            hits[got] = (value, pl[5] if len(pl) > 5 else None, pl.hex(" "))
    if not hits:
        print("  no page responses")
        continue
    print("  %-8s %-14s %-8s %s" % ("page", "value", "status", "raw"))
    for page in sorted(hits):
        value, status, raw = hits[page]
        known = smartlink.PAGES.get(page)
        label = " <- %s" % known[0] if known else ""
        print("  0x%02X     %-14s %-8s %s%s"
              % (page, value, "0x%02X" % status if status is not None else "-",
                 raw, label))
os.close(fd)
