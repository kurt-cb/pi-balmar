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
    """IPv4/IPv6 UDP sender. Resolution is lazy and retried on failure,
    so starting up with the target network down (or a .lan name that
    only resolves on the boat) is fine — sends begin once it works."""

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.target = None
        self.sent = 0
        self.errors = 0

    def send(self, payload: bytes) -> bool:
        try:
            if self.sock is None:
                info = socket.getaddrinfo(self.host, self.port,
                                          type=socket.SOCK_DGRAM)[0]
                self.sock = socket.socket(info[0], socket.SOCK_DGRAM)
                self.target = info[4]
            self.sock.sendto(payload, self.target)
            self.sent += 1
            return True
        except OSError:
            # resolution failure or WiFi drop — keep going, sends resume
            # when the network returns.
            self.errors += 1
            return False
