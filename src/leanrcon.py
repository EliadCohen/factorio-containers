import socket
import struct


class RCONClient:
    """Lightweight Valve RCON client."""

    def __init__(self, host: str, port: int, password: str, timeout: float = 10):
        self._host = host
        self._port = port
        self._password = password
        self._timeout = timeout
        self._sock = None

    def __enter__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self._timeout)
        self._sock.connect((self._host, self._port))
        self._send(1, 3, self._password)   # SERVERDATA_AUTH
        self._recv()                        # auth response
        return self

    def __exit__(self, *_):
        if self._sock:
            self._sock.close()
            self._sock = None

    def _send(self, req_id: int, ptype: int, body: str):
        payload = body.encode() + b"\x00\x00"
        packet = struct.pack("<iii", 8 + len(payload), req_id, ptype) + payload
        self._sock.sendall(packet)

    def _recv(self) -> tuple[int, str]:
        raw_len = self._sock.recv(4)
        length = struct.unpack("<i", raw_len)[0]
        data = b""
        while len(data) < length:
            data += self._sock.recv(length - len(data))
        req_id = struct.unpack("<i", data[:4])[0]
        body = data[8:-2].decode()
        return req_id, body

    def send(self, command: str) -> str:
        """Send a command and return the response body."""
        self._send(2, 2, command)   # SERVERDATA_EXECCOMMAND
        _, body = self._recv()      # SERVERDATA_RESPONSE_VALUE
        return body


def send_command(host: str, port: int, password: str, command: str, timeout: float = 10) -> str:
    """One-shot helper: connect, authenticate, send command, return response."""
    with RCONClient(host, port, password, timeout) as client:
        return client.send(command)
