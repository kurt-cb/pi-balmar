"""Signal K delta construction and UDP/TCP transport."""

import datetime
import json
import socket
import sys
import threading


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


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
                # Prefer IPv4: signalk-server's UDP input binds IPv4 only,
                # so an AAAA record must not win over an A record.
                try:
                    info = socket.getaddrinfo(self.host, self.port,
                                              socket.AF_INET,
                                              socket.SOCK_DGRAM)[0]
                except socket.gaierror:
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


class TcpServerSender:
    """Serve deltas to Signal K over TCP: we listen (dual-stack v4+v6),
    the Signal K server connects as a client (data connection type
    "Signal K", source TCP, host = this Pi, port = ours). This sidesteps
    signalk-server's UDP input being IPv4-only."""

    def __init__(self, port):
        self.clients = []
        self.lock = threading.Lock()
        self.sent = 0
        self.errors = 0
        srv = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except OSError:
            pass
        srv.bind(("::", port))
        srv.listen(4)
        _log(f"serving Signal K deltas on tcp port {port}")
        threading.Thread(target=self._accept_loop, args=(srv,),
                         daemon=True).start()

    def _accept_loop(self, srv):
        while True:
            conn, addr = srv.accept()
            conn.settimeout(1.0)
            with self.lock:
                self.clients.append((conn, addr[0]))
            _log(f"signalk connected from {addr[0]}")

    def send(self, payload: bytes) -> bool:
        any_ok = False
        with self.lock:
            for conn, ip in list(self.clients):
                try:
                    conn.sendall(payload)
                    any_ok = True
                except OSError:
                    self.clients.remove((conn, ip))
                    try:
                        conn.close()
                    except OSError:
                        pass
                    _log(f"signalk client {ip} disconnected")
        if any_ok:
            self.sent += 1
        else:
            self.errors += 1
        return any_ok
