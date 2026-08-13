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
import re
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

# Master-mode health limits. A regulator that wedges stops answering
# entirely, so a sustained low reply rate is the signal to back off.
HEALTH_INTERVAL = 30.0
HEALTH_MIN_SAMPLES = 20
HEALTH_MIN_RATE = 0.5
MUTE_SECONDS = 300.0

# The regulators are powered from an oil-pressure switch, so they exist
# only while an engine runs. Rather than poll a dead bus, idle at a slow
# probe and wake when something answers.
IDLE_PROBE_INTERVAL = 60.0
ACTIVE_TIMEOUT = 20.0
# Identity replies decide Signal K paths; retry rather than let a lost
# reply rename a device.
IDENT_ATTEMPTS = 4
# Probed while idle: the two regulator slots the Balmar master always
# polls, plus the shunt and gateway addresses.
CANDIDATE_ADDRS = (0x81, 0x82, 0x02, 0x03)


def auto_prefix(name, addr):
    """Signal K path prefix for a device from its self-reported name.

    Devices store their own name (cmd 0x04), so a bus can be mapped
    without configuration: "MC-618-STBD" -> electrical.alternators.stbd,
    "BANK-01" -> electrical.batteries.bank01.
    """
    text = (name or "").strip()
    if text.upper().startswith("MC-618"):
        tag = re.sub(r"[^a-z0-9]+", "", text[6:].lower()) or f"{addr:02x}"
        return f"electrical.alternators.{tag}."
    tag = re.sub(r"[^a-z0-9]+", "", text.lower())
    if not tag:
        return f"electrical.devices.x{addr:02x}."
    return f"electrical.batteries.{tag}."


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
    # Names decide Signal K paths, so a dropped identity reply must not
    # silently rename a device. Retry any device still unnamed — replies
    # are regularly lost to collisions with other traffic on this bus.
    for _ in range(IDENT_ATTEMPTS):
        pending = [a for a, info in found.items() if not info["name"]]
        if not pending:
            break
        for addr in pending:
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
    p.add_argument("--emulate-display", action="store_true",
                   help="act as bus master by replaying the Balmar "
                        "display's own poll pattern (its exact page set "
                        "and 258 ms cadence). Required to read MC-618 "
                        "regulators when no display is present, since no "
                        "other master ever requests regulator pages.")
    p.add_argument("--shunt-addr", type=lambda x: int(x, 0), default=0x03,
                   help="address status-polled for the shunt in "
                        "--emulate-display mode (default: 0x03, what the "
                        "display uses)")
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
            args.poll_pages = [smartlink.parse_page(x)
                               for x in str(args.poll).split(",")]
        except ValueError as e:
            p.error(str(e))
    else:
        args.poll_pages = []

    # Address-keyed map (bench/manual use).
    # device_map: addr -> (prefix, device_name-for-$source)
    args.device_map = {}
    for m in args.map:
        try:
            addr, prefix = m.split("=", 1)
            a = int(addr, 0)
            args.device_map[a] = (prefix.rstrip(".") + ".", f"0x{a:02X}")
        except ValueError:
            p.error(f"bad --map '{m}', expected ADDR=PREFIX")

    # Name-keyed config: {"BANK-01": {"report": true, "prefix": "...",
    # "pages": ["0x03", ...]}} — resolved to addresses by bus scan.
    args.name_config = {}
    for name, dev in cfg.get("devices", {}).items():
        try:
            pages = [smartlink.parse_page(x)
                     for x in dev.get("values", dev.get("pages", []))]
        except ValueError as e:
            p.error(f"device '{name}': {e}")
        args.name_config[name] = {
            "report": dev.get("report", True),
            "prefix": dev["prefix"].rstrip(".") + "." if dev.get("prefix") else None,
            "pages": pages,
        }
    if args.name_config and not args.poll_pages and not any(
            d["pages"] for d in args.name_config.values()):
        log("note: config devices have no pages and --poll not given; "
            "passive listening only")

    if not args.device_map and not args.name_config:
        args.device_map = {smartlink.ADDR_SHUNT:
                           ("electrical.batteries.0.", "shunt")}
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
            device_map[addr] = (dev["prefix"], name)
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
    writable = bool(args.poll_pages or args.name_config or args.emulate_display
                    or any(d["pages"] for d in args.name_config.values()))
    last_sent, last_value = {}, {}
    n_frames = n_pages = 0
    last_stats = time.monotonic()
    next_poll = time.monotonic()
    next_scan = None
    poll_idx = 0
    fd = None
    device_map, rotation, unmatched = dict(args.device_map), [], set()
    # Display-emulation master state.
    tx_queue = []
    next_cycle = next_tx = time.monotonic()
    expect, answered = {}, {}          # page requests sent / replied, per addr
    muted = {}                         # addr -> monotonic time to resume
    next_health = time.monotonic() + HEALTH_INTERVAL
    active = not args.emulate_display  # emulation starts idle until something answers
    woke = False
    last_reply = time.monotonic()
    next_probe = time.monotonic()

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

        # Master mode. Frames are queued and paced one per loop pass
        # rather than written back-to-back, so replies keep being read
        # between our transmissions and the inter-frame gap is honoured.
        now_m = time.monotonic()

        # Engines off -> regulators unpowered -> nothing to talk to.
        # Idle at a slow probe instead of polling a dead bus, and wake as
        # soon as anything answers.
        if args.emulate_display and active and \
                now_m - last_reply > ACTIVE_TIMEOUT:
            active = False
            device_map = {}
            log("devices stopped responding (engines off?) — idling, "
                f"probing every {IDLE_PROBE_INTERVAL:.0f}s")
        if args.emulate_display and not active:
            if not tx_queue and now_m >= next_probe:
                next_probe = now_m + IDLE_PROBE_INTERVAL
                for a in CANDIDATE_ADDRS:
                    tx_queue.append(
                        smartlink.build_frame(a, smartlink.CMD_KEEPALIVE))
                    tx_queue.append(
                        smartlink.build_frame(a, smartlink.CMD_STATUS))
            if tx_queue and now_m >= next_tx:
                try:
                    os.write(fd, tx_queue.pop(0))
                except OSError as e:
                    log(f"probe write failed: {e}")
                next_tx = now_m + smartlink.DISPLAY_TX_GAP
            if woke:
                woke = False
                log("device answered — scanning the bus")
                found = scan_bus(fd, parser, CANDIDATE_ADDRS)
                device_map = {}
                for a, info in sorted(found.items()):
                    if not info["name"]:
                        # Publishing under a fallback path risks the data
                        # moving when the name does turn up. Wait instead.
                        log(f"  0x{a:02X} answered but would not identify "
                            f"— skipping until it names itself")
                        continue
                    prefix = auto_prefix(info["name"], a)
                    device_map[a] = (prefix, info["name"])
                    log(f"  0x{a:02X} {info['name']!r} -> {prefix}")
                if device_map:
                    active = True
                    last_reply = time.monotonic()
                    expect.clear()
                    answered.clear()
                    muted.clear()
                    next_cycle = time.monotonic()

        if args.emulate_display and active and device_map:
            if not tx_queue and now_m >= next_cycle:
                addrs = sorted(device_map)
                pages = smartlink.MC618_DISPLAY_PAGES
                # Keepalives always go out — they are what the display
                # sends regardless, and they let a muted device show it
                # is alive again.
                for a in addrs:
                    tx_queue.append(
                        smartlink.build_frame(a, smartlink.CMD_KEEPALIVE))
                if args.shunt_addr is not None:
                    tx_queue.append(smartlink.build_frame(
                        args.shunt_addr, smartlink.CMD_STATUS))
                for a in addrs:
                    if muted.get(a, 0) > now_m:
                        continue
                    page = pages[poll_idx % len(pages)]
                    tx_queue.append(smartlink.build_page_request(page, a))
                    expect[a] = expect.get(a, 0) + 1
                poll_idx += 1
                next_cycle = now_m + smartlink.DISPLAY_CYCLE
            if tx_queue and now_m >= next_tx:
                try:
                    os.write(fd, tx_queue.pop(0))
                except OSError as e:
                    log(f"master write failed: {e}")
                next_tx = now_m + smartlink.DISPLAY_TX_GAP
        elif rotation and now_m >= next_poll:
            addr, page = rotation[poll_idx % len(rotation)]
            poll_idx += 1
            try:
                os.write(fd, smartlink.build_frame(addr, smartlink.CMD_STATUS))
                time.sleep(TX_GAP)
                os.write(fd, smartlink.build_page_request(page, addr))
            except OSError as e:
                log(f"poll write failed: {e}")
            next_poll = now_m + args.poll_interval / len(rotation)

        values = []
        for addr, cmd, payload in parser.feed(data):
            n_frames += 1
            # Any reply at all is proof a device is powered: it marks the
            # bus alive for the idle/active state machine, whether or not
            # it carries data we publish.
            if payload:
                last_reply = time.monotonic()
                if not active:
                    woke = True
            if cmd != smartlink.CMD_PAGE:
                continue
            decoded = smartlink.decode_page_response(payload)
            if decoded is None:
                continue
            page, raw, status = decoded
            n_pages += 1
            answered[addr] = answered.get(addr, 0) + 1
            # The reply layout identifies the device type: MC-618
            # regulators answer with 5 bytes (no status), SG200-family
            # devices with 6. Page meanings and scaling differ between
            # them, so the right table has to be picked per frame.
            table = smartlink.MC618_PAGES if status is None else smartlink.PAGES
            known = table.get(page)
            if args.dump:
                name = known[0] if known else "?"
                st = "--" if status is None else f"0x{status:02X}"
                print(f"addr=0x{addr:02X} page=0x{page:02X} ({name}) "
                      f"raw={raw} status={st}", flush=True)
            if known is None or smartlink.is_not_available(table, page, raw):
                continue
            entry = device_map.get(addr)
            if entry is None:
                continue
            prefix, devname = entry
            _, suffix, scale = known
            values.append((prefix + suffix, scale(raw), devname))

        now = time.monotonic()
        due = []
        for path, value, devname in values:
            if now - last_sent.get(path, 0) >= args.min_period or \
               value != last_value.get(path):
                due.append((path, value, devname))
                last_sent[path] = now
                last_value[path] = value

        if due:
            if args.console:
                ts = time.strftime("%H:%M:%S")
                for path, value, devname in due:
                    print(f"{ts}  [{devname}] {path} = {value}", flush=True)
            if sender:
                delta = build_delta(due)
                ok = any([s.send(delta) for s in senders])
                if ok and args.verbose:
                    print(delta.decode().rstrip(), flush=True)

        # Health check: a regulator that stops answering needs a power
        # cycle, and continuing to poll one that is struggling is what
        # wedged 0x81 during protocol discovery. Back off instead.
        if args.emulate_display and now >= next_health:
            next_health = now + HEALTH_INTERVAL
            for addr in sorted(expect):
                sent, got = expect[addr], answered.get(addr, 0)
                if sent < HEALTH_MIN_SAMPLES:
                    continue
                rate = got / float(sent)
                if rate < HEALTH_MIN_RATE and muted.get(addr, 0) <= now:
                    muted[addr] = now + MUTE_SECONDS
                    log(f"0x{addr:02X} answered {got}/{sent} page reads "
                        f"({rate:.0%}) — pausing page reads for "
                        f"{MUTE_SECONDS:.0f}s (keepalives continue)")
                elif rate >= HEALTH_MIN_RATE and addr in muted:
                    del muted[addr]
                    log(f"0x{addr:02X} answering again ({rate:.0%}); "
                        f"resuming page reads")
            expect.clear()
            answered.clear()

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
