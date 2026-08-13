"""Balmar SG200 SmartLink (RS-485) wire protocol.

Reverse-engineered from live captures (see docs/rs-485 decode.md):

    frame = CD | addr | cmd | len | payload[len] | checksum

- 115200 baud, 8N1
- checksum: two's complement of the byte sum (whole frame sums to 0 mod 256)
- cmd 0x15 with len 1 is a page REQUEST: payload = [page_code]
- cmd 0x15 with len 6 is a page RESPONSE: payload = [page_code, v3, v2, v1, v0, status]
  where v3..v0 is the value as a 32-bit big-endian integer
"""

FRAME_START = 0xCD
ADDR_SHUNT = 0x02
CMD_STATUS = 0x00
CMD_IDENT = 0x04
CMD_KEEPALIVE = 0x11
CMD_PAGE = 0x15

# Raw value meaning "not available" (blank on the display).
NOT_AVAILABLE = -1

# Page codes observed on the wire. Scale converts the raw integer to the
# SI unit used by the Signal K path. Filled in as pages are mapped —
# see docs/rs-485 decode.md.
#   page: (name, signalk_path_suffix, scale_fn)
PAGES = {
    0x03: ("soc", "capacity.stateOfCharge", lambda v: v / 100.0),  # %, confirmed
    0x05: ("voltage", "voltage", lambda v: v / 1000.0),  # mV, confirmed
    0x06: ("current", "current", lambda v: v / 1000.0),  # mA signed, confirmed by load test
}

# MC-618 alternator regulator pages. Different device, different page
# meanings AND different scaling from the SG200 above — do not mix the
# two tables. Every entry below was confirmed against the Balmar display
# (see docs/rs-485 decode.md); voltage was additionally cross-checked
# against the Xantrex measuring the same bank over Xanbus.
#
# Values are emitted in Signal K SI units, so temperature is Kelvin.
KELVIN = 273.15
MC618_PAGES = {
    0x03: ("voltage", "voltage", lambda v: v / 50.0),
    0x04: ("targetVoltage", "targetVoltage", lambda v: v / 50.0),
    0x06: ("alternatorTemperature", "temperature",
           lambda v: v - 114 + KELVIN),
    0x08: ("fieldDrive", "fieldDrive", lambda v: v / 100.0),
    0x02: ("chargeStage", "chargeStage", lambda v: v),
    0x2C: ("absorptionVoltage", "absorptionVoltage", lambda v: v / 50.0),
    0x30: ("floatVoltage", "floatVoltage", lambda v: v / 50.0),
}

# Pages whose raw 255 means "sensor not fitted" rather than a reading
# (confirmed: battery temp showed "--" on the display while page 0x05
# read 255).
MC618_NOT_AVAILABLE = {0x05: 255, 0x06: 255, 0x07: 255}

# The exact page set the Balmar display requests, in its polling order.
# Replaying the display's own behaviour is the safest way to act as bus
# master: sweeping undefined page codes and polling at ~22 reads/s hung a
# regulator until it was power-cycled, while this pattern ran for long
# stretches against both regulators with no ill effect.
MC618_DISPLAY_PAGES = [0x03, 0x04, 0x08, 0x05, 0x06, 0x02, 0x24, 0x2E,
                       0x30, 0x26, 0x2C]

# Cadence measured from the display over a 586 s capture:
#   keepalive to each regulator  3.87/s
#   status poll to the shunt     3.88/s
#   each page                    0.35/s  (11 pages / 2.84 s)
# which is one cycle of [keepalive per device, shunt status, one page per
# device] every 258 ms. Inter-frame gap: median 33 ms, 10th pct 12 ms —
# always above the 10 ms floor below which frames are ignored entirely.
DISPLAY_CYCLE = 0.258
DISPLAY_TX_GAP = 0.030

# Device-name prefix -> page table. Devices report their own name via the
# cmd 0x04 identity read ("MC-618-STBD", "MC-618--POR", "BANK-01").
PAGE_TABLES = {"MC-618": MC618_PAGES}


def pages_for(device_name):
    """Pick the page table for a device by its self-reported name."""
    for prefix, table in PAGE_TABLES.items():
        if device_name and device_name.startswith(prefix):
            return table
    return PAGES


def is_not_available(table, page, raw):
    """True if this raw value is the device's 'no reading' sentinel."""
    if raw == NOT_AVAILABLE:
        return True
    if table is MC618_PAGES:
        return MC618_NOT_AVAILABLE.get(page) == raw
    return False


# Friendly name -> page code, for configs ("values": ["voltage", ...]).
PAGE_BY_NAME = {name: page for page, (name, _, _) in PAGES.items()}
PAGE_BY_NAME.update({name: page for page, (name, _, _) in MC618_PAGES.items()
                     if name not in PAGE_BY_NAME})


def parse_page(token):
    """Accept a page as a friendly name ('voltage') or number ('0x05')."""
    try:
        return int(str(token), 0)
    except ValueError:
        page = PAGE_BY_NAME.get(str(token).lower())
        if page is None:
            raise ValueError(
                f"unknown value '{token}' (known: {', '.join(PAGE_BY_NAME)})")
        return page


def checksum(data) -> int:
    return (-sum(data)) & 0xFF


def build_frame(addr: int, cmd: int, payload: bytes = b"") -> bytes:
    f = bytes([FRAME_START, addr, cmd, len(payload)]) + payload
    return f + bytes([checksum(f)])


def build_page_request(page: int, addr: int = ADDR_SHUNT) -> bytes:
    return build_frame(addr, CMD_PAGE, bytes([page]))


class FrameParser:
    """Incremental frame splitter. feed() bytes, get a list of
    (addr, cmd, payload) tuples for every checksum-valid frame."""

    MAX_PAYLOAD = 32

    def __init__(self):
        self._buf = bytearray()
        self.bad_bytes = 0

    def feed(self, data: bytes):
        self._buf += data
        frames = []
        while True:
            start = self._buf.find(FRAME_START.to_bytes(1, "big"))
            if start < 0:
                self.bad_bytes += len(self._buf)
                self._buf.clear()
                break
            if start:
                self.bad_bytes += start
                del self._buf[:start]
            if len(self._buf) < 5:
                break
            plen = self._buf[3]
            if plen > self.MAX_PAYLOAD:
                self.bad_bytes += 1
                del self._buf[:1]
                continue
            total = 4 + plen + 1
            if len(self._buf) < total:
                break
            frame = bytes(self._buf[:total])
            if sum(frame) % 256 == 0:
                frames.append((frame[1], frame[2], frame[4:-1]))
                del self._buf[:total]
            else:
                self.bad_bytes += 1
                del self._buf[:1]
        return frames


def parse_identity(payload: bytes):
    """Decode a cmd 0x04 identity response payload -> (header_hex, name).
    Observed layout (SG200 shunt): 8 header bytes (status/type/version?)
    followed by a NUL-padded ASCII name ("BANK-01")."""
    if len(payload) < 9:
        return payload.hex(" "), ""
    name = payload[8:].split(b"\x00")[0].decode("ascii", "replace")
    return payload[:8].hex(" "), name


def decode_page_response(payload: bytes):
    """Decode a cmd 0x15 response payload -> (page, raw_value, status)
    or None if it isn't a response (e.g. it's the 1-byte request).

    Two payload layouts are in use (docs/rs-485 decode.md):
      SG200  : page + 4-byte value + status  (6 bytes)
      MC-618 : page + 4-byte value           (5 bytes, no status)
    Requiring 6 bytes silently discarded every regulator reply, so both
    lengths are accepted; status is None when the device does not send
    one."""
    if len(payload) == 6:
        return payload[0], int.from_bytes(payload[1:5], "big", signed=True), \
            payload[5]
    if len(payload) == 5:
        return payload[0], int.from_bytes(payload[1:5], "big", signed=True), \
            None
    return None
