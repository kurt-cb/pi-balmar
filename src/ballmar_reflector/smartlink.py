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
    or None if it isn't a response (e.g. it's the 1-byte request)."""
    if len(payload) != 6:
        return None
    page = payload[0]
    value = int.from_bytes(payload[1:5], "big", signed=True)
    status = payload[5]
    return page, value, status
