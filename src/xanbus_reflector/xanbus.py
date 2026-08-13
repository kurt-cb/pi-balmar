"""Xantrex Xanbus (CAN) wire protocol.

Reverse-engineered from live captures against panel readings — see
`docs/xantrex.md` for the evidence behind every field here.

Xanbus is J1939-framed like NMEA 2000:

    29-bit ID = prio(3) | EDP | DP | PF(8) | PS(8) | SA(8)

PF >= 240 is broadcast and PS forms part of the PGN; PF < 240 means PS is
a destination address. Multi-frame messages use NMEA 2000 fast-packet:
byte0 = (sequence << 5) | frame_index, and on frame 0 byte1 is the total
payload length with the payload starting at byte 2.

Unlike standard NMEA 2000 PGNs, these proprietary payloads are
LITTLE-endian.
"""

CAN_EFF_FLAG = 0x80000000
CAN_ERR_FLAG = 0x20000000
CAN_EFF_MASK = 0x1FFFFFFF

# The SW3000 inverter/charger. The SCP display claims 0x00.
SRC_INVERTER = 0x01


def decode_id(can_id):
    """29-bit CAN ID -> (priority, pgn, source_addr, destination_addr).
    Destination is 255 for broadcast."""
    can_id &= CAN_EFF_MASK
    prio = (can_id >> 26) & 0x7
    dp = (can_id >> 24) & 0x3
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    sa = can_id & 0xFF
    if pf < 240:
        return prio, (dp << 16) | (pf << 8), sa, ps
    return prio, (dp << 16) | (pf << 8) | ps, sa, 255


def u16(p, off):
    return int.from_bytes(p[off:off + 2], "little")


def u32(p, off):
    return int.from_bytes(p[off:off + 4], "little")


def s32(p, off):
    return int.from_bytes(p[off:off + 4], "little", signed=True)


# Field maps, all confirmed against panel readings at two or more
# operating points (docs/xantrex.md). Each entry:
#     (signalk_suffix, offset, reader, scale)
# Values are emitted in Signal K SI units: V, A, W, Hz, Kelvin.
PGN_AC_IN = 126979
PGN_AC_OUT = 126982
PGN_DC = 127172
PGN_DC_MIRROR = 127173      # same data, current sign inverted
PGN_SETTINGS = 75264

FIELDS = {
    PGN_AC_IN: [
        ("acin.voltage", 4, u32, 1000.0),
        ("acin.current", 8, u32, 1000.0),
        ("acin.frequency", 12, u16, 100.0),
        ("acin.power", 16, u32, 1.0),
    ],
    PGN_AC_OUT: [
        ("acout.voltage", 5, u32, 1000.0),
        ("acout.current", 9, u32, 1000.0),
        ("acout.frequency", 14, u16, 100.0),
        ("acout.power", 18, u32, 1.0),
    ],
    PGN_DC: [
        # Current is signed from the inverter's own perspective:
        # positive = charging the bank, negative = being back-fed (e.g.
        # by an alternator). This is the charger's DC terminal, not the
        # battery's net current.
        ("dc.voltage", 2, u32, 1000.0),
        ("dc.current", 6, s32, 1000.0),
        ("dc.power", 10, u32, 1.0),
        ("dc.temperature", 19, u16, 100.0),      # already Kelvin x100
    ],
    PGN_SETTINGS: [
        ("acin.currentLimit", 39, u16, 100.0),   # PowerShare
    ],
}

# Shortest payload a PGN must reach before its fields can be read.
MIN_LEN = {pgn: max(off + 4 for _, off, _, _ in fields)
           for pgn, fields in FIELDS.items()}


class FastPacket:
    """Reassembles NMEA 2000 fast-packet sequences.

    feed() returns a complete payload once the final frame arrives, else
    None. Single-frame messages (which do not carry a fast-packet header)
    are not handled here — they are ignored by the caller.
    """

    def __init__(self):
        self._partial = {}

    def feed(self, pgn, src, data):
        if len(data) < 2:
            return None
        seq, index = data[0] >> 5, data[0] & 0x1F
        key = (pgn, src, seq)
        if index == 0:
            self._partial[key] = [data[1], bytearray(data[2:])]
            return None
        entry = self._partial.get(key)
        if entry is None:
            return None
        expected, buf = entry
        buf += data[1:]
        if len(buf) < expected:
            return None
        del self._partial[key]
        return bytes(buf[:expected])


def decode_payload(pgn, payload):
    """Reassembled payload -> [(signalk_suffix, value), ...]."""
    fields = FIELDS.get(pgn)
    if not fields or len(payload) < MIN_LEN.get(pgn, 0):
        return []
    out = []
    for suffix, off, reader, scale in fields:
        if off + 2 > len(payload):
            continue
        raw = reader(payload, off)
        # 0xFFFF / 0xFFFFFFFF are the "not available" fills seen in the
        # padded regions of these payloads.
        if raw in (0xFFFF, 0xFFFFFFFF):
            continue
        out.append((suffix, raw / scale if scale != 1.0 else raw))
    return out
