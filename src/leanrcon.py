"""
leanrcon — Minimal Valve RCON client for Factorio.

Implements just enough of the RCON protocol (RFC-like Valve spec) to send a
single command and receive its response.  Consumed by factorio_server.py to
issue /server-save and /c rcon.print(...) commands to running containers.

Wire format (little-endian):
  [4 bytes: packet length] [4 bytes: request id] [4 bytes: packet type]
  [body as UTF-8 bytes] [0x00 0x00]   ← two null terminators

Packet types used here:
  3  SERVERDATA_AUTH         — sent once during __enter__ to authenticate
  2  SERVERDATA_EXECCOMMAND  — sent for each command
  2  SERVERDATA_RESPONSE_VALUE — received for command responses (same value)
"""
import socket
import struct


class RCONClient:
    """
    Lightweight Valve RCON client.

    Intended to be used as a context manager::

        with RCONClient("127.0.0.1", 27015, "password") as client:
            response = client.send("/server-save")

    The connection is opened, authenticated, used, and closed within the
    ``with`` block.  A single TCP socket is reused for the entire session.

    Attributes:
        _host (str): Remote hostname or IP.
        _port (int): RCON TCP port.
        _password (str): RCON password set via --rcon-password when starting
            the Factorio server.
        _timeout (float): Socket timeout in seconds for both connect and recv.
        _sock: Active socket, or None when the connection is closed.
    """

    def __init__(self, host: str, port: int, password: str, timeout: float = 10):
        """
        Initialise the client without opening a connection.

        Args:
            host: Hostname or IP of the RCON server.
            port: TCP port the RCON server is listening on.
            password: Plaintext RCON password.
            timeout: Socket timeout in seconds (default 10).
        """
        self._host = host
        self._port = port
        self._password = password
        self._timeout = timeout
        self._sock = None

    def __enter__(self):
        """
        Open the TCP connection and authenticate.

        Sends an AUTH packet (type 3) with the password and discards the
        server's auth response packet.  Raises socket errors on connection
        failure or timeout.

        Returns:
            self — for use in ``with RCONClient(...) as client:`` blocks.
        """
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self._timeout)
        self._sock.connect((self._host, self._port))
        # Packet type 3 = SERVERDATA_AUTH; req_id 1 is arbitrary but non-zero
        self._send(1, 3, self._password)   # SERVERDATA_AUTH
        self._recv()                        # discard auth response packet
        return self

    def __exit__(self, *_):
        """
        Close the socket on context manager exit.

        Ignores all arguments (exception type, value, traceback) so exceptions
        propagate normally after the socket is cleaned up.
        """
        if self._sock:
            self._sock.close()
            self._sock = None

    def _send(self, req_id: int, ptype: int, body: str):
        """
        Encode and transmit one RCON packet.

        Wire format sent over the socket:
          [length:i32][req_id:i32][ptype:i32][body:utf8][0x00][0x00]

        The ``length`` field covers everything after itself:
          8 bytes (req_id + ptype) + len(body) + 2 null bytes.

        Args:
            req_id: Caller-chosen request identifier; echoed back in the
                response so replies can be matched to requests.
            ptype: Packet type integer (3=AUTH, 2=EXECCOMMAND).
            body: Command or password string (must be ASCII-safe for Factorio).
        """
        # Body is UTF-8 encoded and followed by two null bytes per the spec
        payload = body.encode() + b"\x00\x00"
        # Length field = req_id (4) + ptype (4) + payload
        packet = struct.pack("<iii", 8 + len(payload), req_id, ptype) + payload
        self._sock.sendall(packet)

    def _recv(self) -> tuple[int, str]:
        """
        Read one RCON response packet from the socket.

        Reads the 4-byte length prefix first, then reads exactly that many
        bytes in a loop (the OS may fragment the delivery).

        Returns:
            A ``(req_id, body)`` tuple where ``req_id`` is the mirrored
            request id and ``body`` is the decoded response string (trailing
            null bytes stripped).

        Raises:
            socket.timeout: If the server does not respond within self._timeout.
            struct.error: If the server sends malformed length data.
        """
        # First 4 bytes are the little-endian packet length
        raw_len = self._sock.recv(4)
        length = struct.unpack("<i", raw_len)[0]
        # Read until we have the full payload (recv() may return partial data)
        data = b""
        while len(data) < length:
            data += self._sock.recv(length - len(data))
        # Layout: [req_id:4][ptype:4][body][0x00][0x00]
        req_id = struct.unpack("<i", data[:4])[0]
        body = data[8:-2].decode()   # strip the two trailing null bytes
        return req_id, body

    def send(self, command: str) -> str:
        """
        Send a command and return the response body.

        Uses packet type 2 (SERVERDATA_EXECCOMMAND) and request id 2
        (arbitrary but distinct from the auth id of 1).

        Args:
            command: Factorio console command, e.g. ``"/server-save"`` or
                ``"/c rcon.print(#game.connected_players)"``.

        Returns:
            The response string from the server, with trailing whitespace
            intact.

        Raises:
            socket.timeout: If the server does not reply within self._timeout.
        """
        self._send(2, 2, command)   # SERVERDATA_EXECCOMMAND
        _, body = self._recv()      # SERVERDATA_RESPONSE_VALUE
        return body


def send_command(host: str, port: int, password: str, command: str, timeout: float = 10) -> str:
    """
    One-shot helper: connect, authenticate, send command, return response.

    Opens a new connection for every call.  Suitable for infrequent commands
    (e.g. /server-save, player count queries) where connection overhead is
    acceptable.

    Args:
        host: Hostname or IP of the RCON server.
        port: RCON TCP port.
        password: Plaintext RCON password.
        command: Console command to execute.
        timeout: Socket timeout in seconds (default 10).

    Returns:
        Response body string from the server.

    Raises:
        socket.timeout: If connect or recv times out.
        ConnectionRefusedError: If the server is not listening on *port*.
    """
    with RCONClient(host, port, password, timeout) as client:
        return client.send(command)
