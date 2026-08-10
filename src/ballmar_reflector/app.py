"""Balmar SG200 SmartLink (RS-485) -> Signal K UDP reflector.

Reads SmartLink frames from the RS-485 serial port (Waveshare RS485 CAN
HAT (B): /dev/ttySC0), decodes battery data, and sends Signal K delta
JSON over UDP to a Signal K server (data connection type "Signal K",
protocol UDP).

Getting data:
- passive (default): decode the page responses an attached display
  requests (only the currently displayed page is on the bus)
- --poll: act as bus master (required once the display is removed —
  SmartLink slaves only speak when polled, and idle after bus silence)

Identifying devices (--config): devices are configured by the name
stored IN the device itself ("BANK-01"), read via the cmd 0x04 identity
request. At startup in poll mode the bus is scanned, names are matched
to the config, and only configured devices are reported — so the same
config works regardless of bus addresses. Unknown devices are logged.

Standard library only (termios for the serial port).
"""

import argparse
import errno
import json
import os
import select
import sys
import termios
import time

from . import smartlink
from .signalk import TcpServerSender, UdpSender, build_delta

BAUDS = {9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
         57600: termios.B57600, 115200: termios.B115200,
         230400: termios.B230400}

# The shunt ignores frames that arrive <10 ms after the previous one
# (measured: 10 ms gap -> 99% answered, 0 ms -> 0%).
TX_GAP = 0.012
RESCAN_INTERVAL = 60.0


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


def _collect(fd, parser, seconds):
    """Read frames for a fixed window; returns list of (addr, cmd, payload)."""
    out = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.01)
        if r:
            out += parser.feed(os.read(fd, 4096))
    return out


def scan_bus(fd, parser, addrs=range(256)):
    """Probe addresses with a status poll; read identity from responders.
    Returns {addr: {"status": int, "header": hex-str, "name": str}}."""
    found = {}
    for addr in addrs:
        os.write(fd, smartlink.build_frame(addr, smartlink.CMD_STATUS))
        for a, c, pl in _collect(fd, parser, 0.03):
            if c == smartlink.CMD_STATUS and len(pl) == 1:
                found[a] = {"status": pl[0], "header": "", "name": ""}
        time.sleep(TX_GAP)
    for addr in list(found):
        os.write(fd, smartlink.build_frame(addr, smartlink.CMD_IDENT))
        for a, c, pl in _collect(fd, parser, 0.08):
            if c == smartlink.CMD_IDENT and len(pl) > 1 and a in found:
                header, name = smartlink.parse_identity(pl)
                found[a].update(header=header, name=name)
        time.sleep(TX_GAP)
    return found


def discover(args):
    fd = open_serial(args.device, args.baud, writable=True)
    parser = smartlink.FrameParser()
    log("scanning SmartLink addresses 0x00-0xFF (~15 s)...")
    found = scan_bus(fd, parser)
    os.close(fd)
    if not found:
        print("no devices responded")
        return 1
    print(f"{'addr':6} {'status':7} {'name':12} header")
    for addr, info in sorted(found.items()):
        print(f"0x{addr:02X}   0x{info['status']:02X}    "
              f"{info['name'] or '-':12} {info['header']}")
    return 0


def parse_args(argv=None):
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config")
    pre_args, _ = pre.parse_known_args(argv)
    cfg = {}
    if pre_args.config:
        with open(pre_args.config) as f:
            cfg = json.load(f)

    p = argparse.ArgumentParser(
        prog="ballmar-reflector",
        description="Balmar SmartLink (RS-485) -> Signal K UDP reflector")
    p.add_argument("--config", help="JSON config file; CLI flags override it "
                                    "(see config.example.json)")
    p.add_argument("-d", "--device", default="/dev/ttySC0",
                   help="RS-485 serial device (default: /dev/ttySC0)")
    p.add_argument("--baud", type=int, default=115200, choices=sorted(BAUDS),
                   help="baud rate (default: 115200)")
    p.add_argument("--host", help="Signal K server hostname/IP (v4 or v6)")
    p.add_argument("--port", type=int, default=4123,
                   help="UDP port of the Signal K 'Signal K over UDP' "
                        "data connection (default: 4123)")
    p.add_argument("--listen", type=int, metavar="PORT",
                   help="serve deltas as a TCP server on this port; the "
                        "Signal K server connects to us (data connection "
                        "type Signal K, source TCP). Works over IPv6, "
                        "unlike signalk's UDP input.")
    p.add_argument("--map", action="append", default=[], metavar="ADDR=PREFIX",
                   help="map a bus address to a Signal K path prefix "
                        "(manual alternative to config-file device names)")
    p.add_argument("--poll", metavar="PAGES",
                   help="act as bus master: comma-separated page codes "
                        "(e.g. 0x03,0x05,0x06). Required when no display "
                        "is attached.")
    p.add_argument("--poll-interval", type=float, default=1.0,
                   help="seconds per full poll round (default: 1.0)")
    p.add_argument("--min-period", type=float, default=1.0,
                   help="rate-limit per Signal K path, seconds (default: 1.0)")
    p.add_argument("--dump", action="store_true",
                   help="print every decoded frame; no UDP target required")
    p.add_argument("--console", action="store_true",
                   help="print decoded Signal K values to the screen instead "
                        "of (or in addition to) sending UDP; no --host needed")
    p.add_argument("--discover", action="store_true",
                   help="scan the bus for devices (address, status, "
                        "configured name), print a table, and exit")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="print each delta sent")
    p.add_argument("--stats-interval", type=float, default=300.0,
                   help="seconds between stats lines on stderr (0=off)")

    defaults = {k: cfg[k] for k in
                ("device", "baud", "host", "port", "listen", "poll_interval",
                 "min_period", "stats_interval") if k in cfg}
    if cfg.get("poll"):
        defaults["poll"] = ",".join(str(x) for x in cfg["poll"])
    p.set_defaults(**defaults)
    args = p.parse_args(argv)

    if not (args.dump or args.console or args.discover or args.host
            or args.listen):
        p.error("--host or --listen is required unless "
                "--dump/--console/--discover is given")

    if args.poll:
        try:
            args.poll_pages = [int(x, 0) for x in str(args.poll).split(",")]
        except ValueError:
            p.error("--poll expects comma-separated integers, e.g. 0x03,0x05")
    else:
        args.poll_pages = []

    # Address-keyed map (bench/manual use).
    args.device_map = {}
    for m in args.map:
        try:
            addr, prefix = m.split("=", 1)
            args.device_map[int(addr, 0)] = prefix.rstrip(".") + "."
        except ValueError:
            p.error(f"bad --map '{m}', expected ADDR=PREFIX")

    # Name-keyed config: {"BANK-01": {"report": true, "prefix": "...",
    # "pages": ["0x03", ...]}} — resolved to addresses by bus scan.
    args.name_config = {}
    for name, dev in cfg.get("devices", {}).items():
        args.name_config[name] = {
            "report": dev.get("report", True),
            "prefix": dev["prefix"].rstrip(".") + "." if dev.get("prefix") else None,
            "pages": [int(x, 0) for x in dev.get("pages", [])],
        }
    if args.name_config and not args.poll_pages and not any(
            d["pages"] for d in args.name_config.values()):
        log("note: config devices have no pages and --poll not given; "
            "passive listening only")

    if not args.device_map and not args.name_config:
        args.device_map = {smartlink.ADDR_SHUNT: "electrical.batteries.0."}
    return args


def resolve_devices(fd, parser, args):
    """Scan the bus and match configured device names to addresses.
    Returns (device_map, rotation, unmatched_names)."""
    device_map = dict(args.device_map)
    rotation = [(a, pg) for a in args.device_map for pg in args.poll_pages]
    unmatched = set()
    if not args.name_config:
        return device_map, rotation, unmatched

    found = scan_bus(fd, parser)
    by_name = {info["name"]: addr for addr, info in found.items() if info["name"]}
    for addr, info in sorted(found.items()):
        label = info["name"] or "(no name)"
        if info["name"] not in args.name_config:
            log(f"discovered unconfigured device 0x{addr:02X} '{label}' — not reporting")
    for name, dev in args.name_config.items():
        addr = by_name.get(name)
        if addr is None:
            unmatched.add(name)
            log(f"configured device '{name}' not found on bus (will rescan)")
            continue
        log(f"device '{name}' found at 0x{addr:02X}"
            + (f" -> {dev['prefix']}" if dev["report"] and dev["prefix"] else " (report off)"))
        if dev["report"] and dev["prefix"]:
            device_map[addr] = dev["prefix"]
        pages = dev["pages"] or args.poll_pages
        rotation += [(addr, pg) for pg in pages]
    return device_map, rotation, unmatched


def run(args):
    senders = []
    if args.host:
        senders.append(UdpSender(args.host, args.port))
        log(f"reflecting {args.device} -> udp://{args.host}:{args.port}")
    if args.listen:
        senders.append(TcpServerSender(args.listen))
    sender = senders or None

    parser = smartlink.FrameParser()
    writable = bool(args.poll_pages or args.name_config
                    or any(d["pages"] for d in args.name_config.values()))
    last_sent, last_value = {}, {}
    n_frames = n_pages = 0
    last_stats = time.monotonic()
    next_poll = time.monotonic()
    next_scan = None
    poll_idx = 0
    fd = None
    device_map, rotation, unmatched = dict(args.device_map), [], set()

    while True:
        if fd is None:
            try:
                fd = open_serial(args.device, args.baud, writable)
                log(f"listening on {args.device} @ {args.baud}"
                    + (" (master mode)" if writable else " (passive)"))
                if args.name_config:
                    device_map, rotation, unmatched = resolve_devices(fd, parser, args)
                    next_scan = time.monotonic() + RESCAN_INTERVAL if unmatched else None
                else:
                    rotation = [(a, pg) for a in device_map for pg in args.poll_pages]
            except OSError as e:
                log(f"cannot open {args.device}: {e}; retrying in 5s")
                time.sleep(5)
                continue

        if next_scan and time.monotonic() >= next_scan:
            device_map, rotation, unmatched = resolve_devices(fd, parser, args)
            next_scan = time.monotonic() + RESCAN_INTERVAL if unmatched else None

        timeout = 0.2
        if rotation:
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

        # Active polling, display-style: status poll + page request per
        # tick, round-robin over (device, page). The status cadence also
        # wakes a shunt that idled during bus silence.
        if rotation and time.monotonic() >= next_poll:
            addr, page = rotation[poll_idx % len(rotation)]
            poll_idx += 1
            try:
                os.write(fd, smartlink.build_frame(addr, smartlink.CMD_STATUS))
                time.sleep(TX_GAP)
                os.write(fd, smartlink.build_page_request(page, addr))
            except OSError as e:
                log(f"poll write failed: {e}")
            next_poll = time.monotonic() + args.poll_interval / len(rotation)

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
            if known is None or raw == smartlink.NOT_AVAILABLE:
                continue
            prefix = device_map.get(addr)
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

        if due:
            if args.console:
                ts = time.strftime("%H:%M:%S")
                for path, value in due:
                    print(f"{ts}  {path} = {value}", flush=True)
            if sender:
                delta = build_delta(due)
                ok = any([s.send(delta) for s in senders])
                if ok and args.verbose:
                    print(delta.decode().rstrip(), flush=True)

        if args.stats_interval > 0 and now - last_stats >= args.stats_interval:
            tx = " ".join(f"{type(s).__name__}: sent={s.sent} errors={s.errors}"
                          for s in senders)
            log(f"stats: frames={n_frames} page_responses={n_pages} "
                f"bad_bytes={parser.bad_bytes} {tx}")
            last_stats = now


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.discover:
            return discover(args)
        return run(args) or 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
