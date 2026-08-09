"""Balmar SG200 SmartLink (RS-485) -> Signal K UDP reflector.

Reads SmartLink frames from the RS-485 serial port (Waveshare RS485 CAN
HAT (B): /dev/ttySC0), decodes battery data, and sends Signal K delta
JSON over UDP to a Signal K server (data connection type "Signal K",
protocol UDP).

Two ways to get data:
- passive (default): decode the page responses the display already
  requests (only the currently displayed page is on the bus)
- --poll: act as bus master and request pages ourselves; required once
  the display is removed, since SmartLink slaves only speak when polled

Standard library only (termios for the serial port).
"""

import argparse
import errno
import os
import select
import sys
import termios
import time

from . import smartlink
from .signalk import UdpSender, build_delta

BAUDS = {9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
         57600: termios.B57600, 115200: termios.B115200,
         230400: termios.B230400}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def open_serial(device, baud, writable):
    flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOCTTY | os.O_NONBLOCK
    fd = os.open(device, flags)
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0                                  # raw
    a[2] = termios.CREAD | termios.CLOCAL | termios.CS8     # 8N1
    a[4] = a[5] = BAUDS[baud]
    termios.tcsetattr(fd, termios.TCSANOW, a)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="ballmar-reflector",
        description="Balmar SG200 SmartLink (RS-485) -> Signal K UDP reflector")
    p.add_argument("-d", "--device", default="/dev/ttySC0",
                   help="RS-485 serial device (default: /dev/ttySC0)")
    p.add_argument("--baud", type=int, default=115200, choices=sorted(BAUDS),
                   help="baud rate (default: 115200)")
    p.add_argument("--host", help="Signal K server hostname/IP")
    p.add_argument("--port", type=int, default=4123,
                   help="UDP port of the Signal K 'Signal K over UDP' "
                        "data connection (default: 4123)")
    p.add_argument("--map", action="append", default=[], metavar="ADDR=PREFIX",
                   help="map a SmartLink device address to a Signal K path "
                        "prefix, repeatable; hex ok. Default: "
                        "0x02=electrical.batteries.house. Example for two "
                        "MC-618s: --map 0x00=electrical.alternators.0 "
                        "--map 0x01=electrical.alternators.1")
    p.add_argument("--poll", metavar="PAGES",
                   help="act as bus master: comma-separated page codes to "
                        "request (e.g. '3' or '1,2,3,4'); hex ok (0x03). "
                        "Use when the display is removed.")
    p.add_argument("--poll-interval", type=float, default=1.0,
                   help="seconds between poll rounds (default: 1.0)")
    p.add_argument("--min-period", type=float, default=1.0,
                   help="rate-limit per Signal K path, seconds (default: 1.0)")
    p.add_argument("--dump", action="store_true",
                   help="print every decoded frame; no UDP target required")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="print each delta sent")
    p.add_argument("--stats-interval", type=float, default=300.0,
                   help="seconds between stats lines on stderr (0=off)")
    args = p.parse_args(argv)

    if not args.dump and not args.host:
        p.error("--host is required unless --dump is given")
    if args.poll:
        try:
            args.poll_pages = [int(x, 0) for x in args.poll.split(",")]
        except ValueError:
            p.error("--poll expects comma-separated integers, e.g. 3 or 0x03,0x04")
    else:
        args.poll_pages = []

    args.device_map = {}
    for m in (args.map or ["0x02=electrical.batteries.house"]):
        try:
            addr, prefix = m.split("=", 1)
            args.device_map[int(addr, 0)] = prefix.rstrip(".") + "."
        except ValueError:
            p.error(f"bad --map '{m}', expected ADDR=PREFIX (e.g. 0x02=electrical.batteries.house)")
    return args


def run(args):
    sender = UdpSender(args.host, args.port) if args.host else None
    if sender:
        maps = ", ".join(f"0x{a:02X}->{p}" for a, p in args.device_map.items())
        log(f"reflecting {args.device} -> udp://{args.host}:{args.port} ({maps})")

    parser = smartlink.FrameParser()
    last_sent = {}          # path -> monotonic time
    last_value = {}         # path -> value
    n_frames = n_pages = 0
    last_stats = time.monotonic()
    next_poll = time.monotonic()
    poll_idx = 0
    fd = None

    while True:
        if fd is None:
            try:
                fd = open_serial(args.device, args.baud, writable=bool(args.poll_pages))
                log(f"listening on {args.device} @ {args.baud}")
            except OSError as e:
                log(f"cannot open {args.device}: {e}; retrying in 5s")
                time.sleep(5)
                continue

        timeout = 0.2
        if args.poll_pages:
            timeout = max(0.0, min(timeout, next_poll - time.monotonic()))
        try:
            readable, _, _ = select.select([fd], [], [], timeout)
            data = os.read(fd, 4096) if readable else b""
        except OSError as e:
            if e.errno in (errno.EIO, errno.ENODEV, errno.EBADF):
                log(f"{args.device} went away ({e}); reopening")
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
                time.sleep(2)
                continue
            raise

        # Active polling: one page per round-robin tick.
        if args.poll_pages and time.monotonic() >= next_poll:
            page = args.poll_pages[poll_idx % len(args.poll_pages)]
            poll_idx += 1
            try:
                os.write(fd, smartlink.build_page_request(page))
            except OSError as e:
                log(f"poll write failed: {e}")
            next_poll = time.monotonic() + args.poll_interval / max(1, len(args.poll_pages))

        values = []
        for addr, cmd, payload in parser.feed(data):
            n_frames += 1
            if cmd != smartlink.CMD_PAGE:
                continue
            decoded = smartlink.decode_page_response(payload)
            if decoded is None:
                continue
            page, raw, status = decoded
            n_pages += 1
            known = smartlink.PAGES.get(page)
            if args.dump:
                name = known[0] if known else "?"
                print(f"addr=0x{addr:02X} page=0x{page:02X} ({name}) "
                      f"raw={raw} status=0x{status:02X}", flush=True)
            if known is None:
                continue
            # -1 means "not available" (blank display page). For current
            # (0x04) a genuine -1 mA is indistinguishable, but dropping one
            # reading in a thousand is harmless.
            if raw == smartlink.NOT_AVAILABLE:
                continue
            prefix = args.device_map.get(addr)
            if prefix is None:
                continue
            _, suffix, scale = known
            values.append((prefix + suffix, scale(raw)))

        now = time.monotonic()
        due = []
        for path, value in values:
            if now - last_sent.get(path, 0) >= args.min_period or \
               value != last_value.get(path):
                due.append((path, value))
                last_sent[path] = now
                last_value[path] = value

        if due and sender:
            delta = build_delta(due)
            if sender.send(delta) and args.verbose:
                print(delta.decode().rstrip(), flush=True)

        if args.stats_interval > 0 and now - last_stats >= args.stats_interval:
            log(f"stats: frames={n_frames} page_responses={n_pages} "
                f"bad_bytes={parser.bad_bytes}"
                + (f" udp_sent={sender.sent} udp_errors={sender.errors}"
                   if sender else ""))
            last_stats = now


def main(argv=None):
    args = parse_args(argv)
    try:
        return run(args) or 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
