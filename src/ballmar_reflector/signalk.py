"""Signal K delta construction and UDP transport."""

import datetime
import json
import socket


def build_delta(values, label="balmar-sg200"):
    """values: list of (path, value). Returns one newline-terminated
    JSON delta, ready to send to a Signal K UDP connection."""
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")
    delta = {
        "updates": [{
            "source": {"label": label, "type": "serial"},
            "timestamp": ts,
            "values": [{"path": p, "value": v} for p, v in values],
        }]
    }
    return (json.dumps(delta, separators=(",", ":")) + "\n").encode("ascii")


class UdpSender:
    def __init__(self, host, port):
        self.target = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sent = 0
        self.errors = 0

    def send(self, payload: bytes) -> bool:
        try:
            self.sock.sendto(payload, self.target)
            self.sent += 1
            return True
        except OSError:
            # WiFi drop etc. — caller keeps going, sends resume when the
            # network returns.
            self.errors += 1
            return False
