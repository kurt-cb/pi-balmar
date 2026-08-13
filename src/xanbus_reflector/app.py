"""Xantrex Xanbus (CAN) -> Signal K UDP reflector.

Reads the SW3000 inverter/charger over the Waveshare HAT's CAN side and
publishes AC input, AC output and DC measurements as Signal K deltas.

Deliberately a separate daemon from the Balmar SmartLink reflector: it
touches only `can0`, so SmartLink probing on /dev/ttySC0 cannot disturb
it (and vice versa).

The CAN interface should be up at 250 kbit/s in listen-only mode, so the
Pi never transmits on a live inverter bus:

    ip link set can0 down
    ip link set can0 type can bitrate 250000 listen-only on
    ip link set can0 up

Standard library only (socket.AF_CAN).
"""

import argparse
import errno
import json
import socket
import struct
import sys
import time

from . import xanbus
from ballmar_reflector.signalk import TcpServerSender, UdpSender, build_delta

CAN_FRAME_FMT = "<IB3x8s"
CAN_FRAME_SIZE = 16


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def parse_args(argv=None):
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config")
    pre_args, _ = pre.parse_known_args(argv)
    cfg = {}
    if pre_args.config:
        with open(pre_args.config) as f:
            cfg = json.load(f)

    p = argparse.ArgumentParser(
        prog="xanbus-reflector",
        description="Xantrex Xanbus (CAN) -> Signal K reflector")
    p.add_argument("--config", help="JSON config file; CLI flags override it")
    p.add_argument("-i", "--interface", default="can0",
                   help="CAN interface (default: can0)")
    p.add_argument("--host", help="Signal K server hostname/IP")
    p.add_argument("--port", type=int, default=4123,
                   help="UDP port of the Signal K 'Signal K over UDP' data "
                        "connection (default: 4123)")
    p.add_argument("--listen", type=int, metavar="PORT",
                   help="serve deltas as a TCP server instead; the Signal K "
                        "server connects to us")
    p.add_argument("--prefix", default="electrical.inverters.sw3000",
                   help="Signal K path prefix "
                        "(default: electrical.inverters.sw3000)")
    p.add_argument("--source-addr", type=lambda x: int(x, 0),
                   default=xanbus.SRC_INVERTER,
                   help="Xanbus source address to report "
                        "(default: 0x01, the SW3000)")
    p.add_argument("--min-period", type=float, default=1.0,
                   help="rate-limit per path, seconds (default: 1.0)")
    p.add_argument("--console", action="store_true",
                   help="print decoded values instead of sending")
    p.add_argument("--dump", action="store_true",
                   help="print every decoded PGN payload (discovery)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="print each delta sent")
    p.add_argument("--stats-interval", type=float, default=300.0,
                   help="seconds between stats lines (0=off)")

    defaults = {k: cfg[k] for k in
                ("interface", "host", "port", "listen", "prefix",
                 "min_period", "stats_interval") if k in cfg}
    p.set_defaults(**defaults)
    args = p.parse_args(argv)

    if not (args.console or args.dump or args.host or args.listen):
        p.error("--host or --listen is required unless --console/--dump")
    args.prefix = args.prefix.rstrip(".") + "."
    return args


def open_can(interface):
    sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    sock.settimeout(1.0)
    sock.bind((interface,))
    return sock


def run(args):
    senders = []
    if args.host:
        senders.append(UdpSender(args.host, args.port))
        log(f"reflecting {args.interface} -> udp://{args.host}:{args.port}")
    if args.listen:
        senders.append(TcpServerSender(args.listen))

    reassembler = xanbus.FastPacket()
    last_sent, last_value = {}, {}
    n_frames = n_decoded = 0
    last_stats = time.monotonic()
    sock = None

    while True:
        if sock is None:
            try:
                sock = open_can(args.interface)
                log(f"listening on {args.interface}")
            except OSError as e:
                log(f"cannot open {args.interface}: {e}; retrying in 5s")
                time.sleep(5)
                continue

        try:
            frame = sock.recv(CAN_FRAME_SIZE)
        except socket.timeout:
            frame = None
        except OSError as e:
            if e.errno in (errno.ENODEV, errno.ENETDOWN, errno.EBADF):
                log(f"{args.interface} went away ({e}); reopening")
                try:
                    sock.close()
                except OSError:
                    pass
                sock = None
                time.sleep(2)
                continue
            raise

        values = []
        if frame:
            can_id, dlc, data = struct.unpack(CAN_FRAME_FMT, frame)
            if not (can_id & xanbus.CAN_ERR_FLAG):
                n_frames += 1
                _, pgn, src, _ = xanbus.decode_id(can_id)
                if src == args.source_addr and pgn in xanbus.FIELDS:
                    payload = reassembler.feed(pgn, src, data[:dlc])
                    if payload:
                        decoded = xanbus.decode_payload(pgn, payload)
                        if args.dump:
                            print(f"pgn={pgn} src=0x{src:02X} "
                                  f"len={len(payload)} {payload.hex(' ')}",
                                  flush=True)
                        if decoded:
                            n_decoded += 1
                        for suffix, value in decoded:
                            values.append((args.prefix + suffix, value,
                                           "sw3000"))

        now = time.monotonic()
        due = []
        for path, value, dev in values:
            if now - last_sent.get(path, 0) >= args.min_period or \
                    value != last_value.get(path):
                due.append((path, value, dev))
                last_sent[path] = now
                last_value[path] = value

        if due:
            if args.console:
                ts = time.strftime("%H:%M:%S")
                for path, value, _ in due:
                    print(f"{ts}  {path} = {value}", flush=True)
            if senders:
                delta = build_delta(due, label="xantrex-sw3000")
                ok = any([s.send(delta) for s in senders])
                if ok and args.verbose:
                    print(delta.decode().rstrip(), flush=True)

        if args.stats_interval > 0 and now - last_stats >= args.stats_interval:
            tx = " ".join(f"{type(s).__name__}: sent={s.sent} errors={s.errors}"
                          for s in senders)
            log(f"stats: can_frames={n_frames} decoded={n_decoded} {tx}")
            last_stats = now


def main(argv=None):
    args = parse_args(argv)
    try:
        return run(args) or 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
