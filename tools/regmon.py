"""Poll the MC-618's readable pages in a loop and report what moves.

Separates live measurements from configuration without needing the BLE
app: config pages hold still, measurements drift, and RPM should swing
hard and immediately when the throttle is changed.

Only cmd 0x15 page reads are sent -- a known read operation.

    regmon.py <addr> <seconds> [pages]
"""
import os
import select
import sys
import termios
import time

sys.path.insert(0, "/root/pi-ballmar/src")
from ballmar_reflector import smartlink

addr = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x81
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
# Pages that answered during the full sweep.
PAGES = [int(x, 0) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else [
    0x02, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0F,
    0x24, 0x25, 0x26, 0x29, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A,
]
TX_GAP = 0.014

fd = os.open("/dev/ttySC0", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd)
a[0] = a[1] = a[3] = 0
a[2] = termios.CREAD | termios.CLOCAL | termios.CS8
a[4] = a[5] = termios.B115200
termios.tcsetattr(fd, termios.TCSANOW, a)
termios.tcflush(fd, termios.TCIOFLUSH)

parser = smartlink.FrameParser()
parser.MAX_PAYLOAD = 250


def read_page(page):
    os.write(fd, smartlink.build_page_request(page, addr))
    end = time.time() + 0.030
    value = None
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.005)
        if not r:
            continue
        for a_, c_, pl in parser.feed(os.read(fd, 4096)):
            if a_ == addr and c_ == smartlink.CMD_PAGE and len(pl) >= 5 \
                    and pl[0] == page:
                value = int.from_bytes(pl[1:5], "big", signed=True)
    time.sleep(TX_GAP)
    return value


history = {}
first = {}
t0 = time.time()
print("polling 0x%02X, %d pages, %.0fs -- change the throttle now\n"
      % (addr, len(PAGES), secs), flush=True)

while time.time() - t0 < secs:
    round_t = time.time() - t0
    for page in PAGES:
        v = read_page(page)
        if v is None:
            continue
        if page not in first:
            first[page] = v
            history[page] = v
            continue
        if v != history[page]:
            delta = v - first[page]
            print("%7.1fs  page 0x%02X  %-12d (was %-12d start %-12d "
                  "delta %+d)" % (round_t, page, v, history[page], first[page],
                                  delta), flush=True)
            history[page] = v

print("\n=== summary: total movement from first reading ===")
print("%-8s %-14s %-14s %s" % ("page", "first", "last", "changed"))
for page in sorted(first):
    moved = "YES" if history[page] != first[page] else ""
    print("0x%02X     %-14d %-14d %s" % (page, first[page], history[page], moved))
os.close(fd)
