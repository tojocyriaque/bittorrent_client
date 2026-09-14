"""Interactive BitTorrent client console.

Start it with ``python client.py`` and use ``help`` to discover commands.
"""

import cmd
from contextlib import contextmanager
import os
import shlex
import select
import signal
import socket
import sys
import termios
import threading
from pathlib import Path
from typing import TextIO

import torrent


PROMPT = "[bittorrent]# "
PEER_MESSAGE_IDS = {
    "choke": 0,
    "unchoke": 1,
    "interested": 2,
    "not_interested": 3,
    "have": 4,
    "bitfield": 5,
    "request": 6,
    "piece": 7,
    "cancel": 8,
}
PEER_MESSAGE_NAMES = {message_id: name.replace("_", "-") for name, message_id in PEER_MESSAGE_IDS.items()}


def parse_port(value: str) -> int:
    """Parse a user-provided TCP port with a useful error message."""
    try:
        port = int(value)
    except ValueError as error:
        raise ValueError("Port must be an integer.") from error
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return port


def validate_torrent_path(value: str) -> Path:
    """Validate a torrent path before the tracker is contacted."""
    path = Path(value).expanduser()
    if not path.is_file():
        raise ValueError(f"File not found: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"Torrent file is empty: {path}")
    if path.suffix.lower() != ".torrent":
        raise ValueError("File must have a .torrent extension.")
    return path


class BitTorrentShell(cmd.Cmd):
    """A small, stateful console inspired by iwctl."""

    prompt = PROMPT
    intro = (
        "Interactive BitTorrent client. Type 'help' to list commands, "
        "or 'scan <torrent-file>' to get started. Press Ctrl-S to cancel "
        "a scan, connection, or handshake."
    )

    def __init__(self, stdout: TextIO | None = None) -> None:
        super().__init__(stdout=stdout)
        # One client owns the torrent identity, peer connection, and tracker
        # operations for the lifetime of this shell.  In particular, the
        # info_hash and peer_id calculated by ``scan`` are needed later for
        # the peer-wire handshake in ``connect``.
        self.client = torrent.TorrentClient()
        self.peers: list[tuple[str, int]] = []
        self.torrent_file: Path | None = None
        self.connection: torrent.TorrentClient | None = None
        self.connected_peer: tuple[str, int] | None = None
        self.handshake_complete = False

    def emptyline(self) -> None:
        """Do nothing when Enter is pressed on an empty prompt."""

    def default(self, line: str) -> None:
        self.stdout.write(f"Unknown command: {line}. Type 'help'.\n")

    def _arguments(self, argument: str, usage: str) -> list[str] | None:
        try:
            return shlex.split(argument)
        except ValueError as error:
            self.stdout.write(f"Invalid syntax: {error}.\nUsage: {usage}\n")
            return None

    @contextmanager
    def _ctrl_s_cancellation(self):
        """Temporarily turn Ctrl-S into an interrupt for a network call."""
        if not sys.stdin.isatty():
            yield
            return

        try:
            stdin_fd = sys.stdin.fileno()
            previous_settings = termios.tcgetattr(stdin_fd)
        except (OSError, termios.error):
            yield
            return

        settings = termios.tcgetattr(stdin_fd)
        settings[0] &= ~termios.IXON  # Ctrl-S normally pauses terminal output.
        settings[3] &= ~(termios.ICANON | termios.ECHO)
        settings[6][termios.VMIN] = 1
        settings[6][termios.VTIME] = 0
        stop_reader = threading.Event()

        def watch_for_cancel() -> None:
            while not stop_reader.is_set():
                readable, _, _ = select.select([stdin_fd], [], [], 0.1)
                if readable and os.read(stdin_fd, 1) == b"\x13":
                    os.kill(os.getpid(), signal.SIGINT)
                    return

        try:
            termios.tcsetattr(stdin_fd, termios.TCSANOW, settings)
            reader = threading.Thread(target=watch_for_cancel, daemon=True)
            reader.start()
            yield
        finally:
            stop_reader.set()
            if "reader" in locals():
                reader.join(timeout=0.2)
            termios.tcsetattr(stdin_fd, termios.TCSANOW, previous_settings)

    def do_scan(self, argument: str) -> None:
        """scan <torrent-file>

        Contact the torrent tracker and replace the peers stored in memory.
        """
        arguments = self._arguments(argument, "scan <torrent-file>")
        if arguments is None:
            return
        if len(arguments) != 1:
            self.stdout.write("Usage: scan <torrent-file>\n")
            return
        try:
            torrent_file = validate_torrent_path(arguments[0])
            # Changing torrents invalidates the active peer-wire session, but
            # keeps the same TorrentClient and its newly generated identity.
            self._disconnect_current()
            with self._ctrl_s_cancellation():
                peers = self.client.contact_peers_from_torrent(str(torrent_file))
        except KeyboardInterrupt:
            self.stdout.write("Scan cancelled.\n")
            return
        except (OSError, ValueError, KeyError, UnicodeDecodeError, torrent.requests.RequestException) as error:
            self.stdout.write(f"Scan failed: {error}\n")
            return

        self.torrent_file = torrent_file
        self.peers = peers
        self.stdout.write(f"Scan complete: {len(peers)} peer(s) found.\n")
        if peers:
            self.stdout.write("Use 'peers' to list them or 'connect <number>'.\n")

    def do_peers(self, argument: str) -> None:
        """peers

        Display peers found by the most recent scan.
        """
        arguments = self._arguments(argument, "peers")
        if arguments is None:
            return
        if arguments:
            self.stdout.write("Usage: peers\n")
            return
        if not self.peers:
            self.stdout.write("No peers in memory. Run 'scan <torrent-file>' first.\n")
            return

        index_width = len(str(len(self.peers)))
        host_width = max(len("HOST"), *(len(host) for host, _ in self.peers))
        self.stdout.write(f"{'#':>{index_width}}  {'HOST':<{host_width}}  PORT\n")
        self.stdout.write(f"{'-' * index_width}  {'-' * host_width}  {'-' * 5}\n")
        for index, (host, port) in enumerate(self.peers, start=1):
            self.stdout.write(f"{index:>{index_width}}  {host:<{host_width}}  {port}\n")

    def do_connect(self, argument: str) -> None:
        """connect <peer-number> | connect <host> <port>

        Connect a peer found with 'scan', or a manually supplied host and port.
        """
        arguments = self._arguments(argument, "connect <peer-number> | connect <host> <port>")
        if arguments is None:
            return
        try:
            host, port = self._connection_target(arguments)
        except ValueError as error:
            self.stdout.write(f"{error}\n")
            return
        if self.torrent_file is None:
            self.stdout.write("No torrent in memory. Run 'scan <torrent-file>' first.\n")
            return

        self._disconnect_current()
        self.stdout.write(f"Connecting to {host}:{port}...\n")
        try:
            with self._ctrl_s_cancellation():
                self.client.connect_to_peer(host, port, debug=False)
        except KeyboardInterrupt:
            self.client.disconnect()
            self.stdout.write("Connection cancelled.\n")
            return
        except (socket.gaierror, OSError, ConnectionError, ValueError) as error:
            self.client.disconnect()
            self.stdout.write(f"Connection failed: {error}\n")
            return

        self.connection = self.client
        self.connected_peer = (host, port)
        self.handshake_complete = False
        self.stdout.write(f"Connected to {host}:{port}.\n")

    def do_handshake(self, argument: str) -> None:
        """handshake

        Send the BitTorrent handshake to the current peer and verify that it
        responds with the info hash of the torrent selected by ``scan``.
        """
        if argument.strip():
            self.stdout.write("Usage: handshake\n")
            return
        if not self.connection:
            self.stdout.write("No peer is connected. Use 'connect' first.\n")
            return

        self.stdout.write("Establishing BitTorrent handshake...\n")
        try:
            with self._ctrl_s_cancellation():
                self.connection.send_handshake()
                handshake = self.connection.receive_handshake()
                self.connection.verify_info(handshake)
        except KeyboardInterrupt:
            self._disconnect_current()
            self.stdout.write("Handshake cancelled.\n")
            return

        except (OSError, ConnectionError, ValueError) as error:
            self._disconnect_current()
            self.stdout.write(f"Handshake failed: {error}\n")
            return

        self.handshake_complete = True
        self.stdout.write("Handshake complete.\n")

    do_establish = do_handshake

    def do_receive(self, argument: str) -> None:
        """receive

        Receive and display one peer-wire message. Use Ctrl-S to cancel.
        """
        if argument.strip():
            self.stdout.write("Usage: receive\n")
            return
        if not self._peer_protocol_ready():
            return

        try:
            with self._ctrl_s_cancellation():
                message = self.connection.recv_msg()
        except KeyboardInterrupt:
            self._disconnect_current()
            self.stdout.write("Receive cancelled.\n")
            return
        except (OSError, ConnectionError, ValueError) as error:
            self._disconnect_current()
            self.stdout.write(f"Receive failed: {error}\n")
            return

        if message is None:
            self.stdout.write("Received keep-alive.\n")
            return

        msg_id, payload = message
        try:
            decoded = self._decode_peer_payload(msg_id, payload)
        except ValueError as error:
            self.stdout.write(f"Invalid peer message: {error}\n")
            return
        name = PEER_MESSAGE_NAMES[msg_id]
        suffix = "" if decoded is None else f": {decoded}"
        self.stdout.write(f"Received {name}{suffix}\n")

    def do_send(self, argument: str) -> None:
        """send <message> [arguments]

        Send keepalive, choke, unchoke, interested, not-interested, have,
        bitfield, request, piece, or cancel. Type 'help send' for details.
        """
        arguments = self._arguments(argument, "send <message> [arguments]")
        if arguments is None:
            return
        if not self._peer_protocol_ready():
            return
        try:
            msg_id, payload, label = self._outgoing_message(arguments)
        except ValueError as error:
            self.stdout.write(f"{error}\n")
            return

        try:
            with self._ctrl_s_cancellation():
                self._send_peer_message(msg_id, payload)
        except KeyboardInterrupt:
            self._disconnect_current()
            self.stdout.write("Send cancelled.\n")
            return
        except (OSError, ConnectionError, ValueError) as error:
            self._disconnect_current()
            self.stdout.write(f"Send failed: {error}\n")
            return
        self.stdout.write(f"Sent {label}.\n")

    def _peer_protocol_ready(self) -> bool:
        if not self.connection:
            self.stdout.write("No peer is connected. Use 'connect' first.\n")
            return False
        if not self.handshake_complete:
            self.stdout.write("Handshake is required. Use 'handshake' first.\n")
            return False
        return True

    @staticmethod
    def _outgoing_message(arguments: list[str]) -> tuple[int | None, object, str]:
        if not arguments:
            raise ValueError("Usage: send <message> [arguments]")
        name = arguments[0].lower().replace("-", "_")
        values = arguments[1:]
        if name == "keepalive":
            if values:
                raise ValueError("Usage: send keepalive")
            return None, None, "keep-alive"
        if name not in PEER_MESSAGE_IDS:
            raise ValueError("Unknown message. Use 'help send' for supported messages.")

        msg_id = PEER_MESSAGE_IDS[name]
        if msg_id in {0, 1, 2, 3}:
            if values:
                raise ValueError(f"Usage: send {name.replace('_', '-')}")
            return msg_id, None, name.replace("_", "-")
        if msg_id == 4:
            if len(values) != 1:
                raise ValueError("Usage: send have <piece-index>")
            return msg_id, BitTorrentShell._uint32(values[0], "piece index"), "have"
        if msg_id == 5:
            if len(values) != 1 or not values[0] or set(values[0]) - {"0", "1"}:
                raise ValueError("Usage: send bitfield <bits> (a sequence of 0 and 1)")
            bits = values[0]
            padding = (-len(bits)) % 8
            return msg_id, int(bits + "0" * padding, 2).to_bytes((len(bits) + padding) // 8), "bitfield"
        if msg_id in {6, 8}:
            if len(values) != 3:
                raise ValueError(f"Usage: send {name} <index> <begin> <length>")
            return msg_id, tuple(BitTorrentShell._uint32(value, label) for value, label in zip(values, ("index", "begin", "length"))), name
        if len(values) != 3:
            raise ValueError("Usage: send piece <index> <begin> <hex-data>")
        try:
            block = bytes.fromhex(values[2])
        except ValueError as error:
            raise ValueError("Piece data must be hexadecimal bytes.") from error
        return msg_id, (BitTorrentShell._uint32(values[0], "index"), BitTorrentShell._uint32(values[1], "begin"), block), "piece"

    @staticmethod
    def _uint32(value: str, label: str) -> int:
        try:
            number = int(value)
        except ValueError as error:
            raise ValueError(f"{label.capitalize()} must be an integer.") from error
        if not 0 <= number <= 0xFFFFFFFF:
            raise ValueError(f"{label.capitalize()} must be between 0 and 4294967295.")
        return number

    def _send_peer_message(self, msg_id: int | None, message) -> None:
        if self.connection is None or self.connection.socket_conn is None:
            raise ConnectionError("No peer is connected")
        if msg_id is None:
            self.connection.socket_conn.sendall(b"\x00\x00\x00\x00")
            return

        payload = self._encode_peer_payload(msg_id, message)
        frame = (len(payload) + 1).to_bytes(4, byteorder="big") + bytes([msg_id]) + payload
        self.connection.socket_conn.sendall(frame)

    @staticmethod
    def _encode_peer_payload(msg_id: int, message) -> bytes:
        if msg_id in {0, 1, 2, 3}:
            return b""
        if msg_id == 4:
            return message.to_bytes(4, byteorder="big")
        if msg_id == 5:
            return message
        if msg_id in {6, 8}:
            return b"".join(value.to_bytes(4, byteorder="big") for value in message)
        if msg_id == 7:
            index, begin, block = message
            return index.to_bytes(4, byteorder="big") + begin.to_bytes(4, byteorder="big") + block
        raise ValueError(f"Unknown message ID: {msg_id}")

    @staticmethod
    def _decode_peer_payload(msg_id: int, payload: bytes):
        if msg_id in {0, 1, 2, 3}:
            if payload:
                raise ValueError("Message must not have a payload")
            return None
        if msg_id == 4:
            if len(payload) != 4:
                raise ValueError("Invalid have payload")
            return int.from_bytes(payload, byteorder="big")
        if msg_id == 5:
            return [[int(byte & (1 << bit) != 0) for bit in range(7, -1, -1)] for byte in payload]
        if msg_id in {6, 8}:
            if len(payload) != 12:
                raise ValueError("Invalid request or cancel payload")
            return tuple(int.from_bytes(payload[index : index + 4], byteorder="big") for index in range(0, 12, 4))
        if msg_id == 7:
            if len(payload) < 8:
                raise ValueError("Invalid piece payload")
            return (
                int.from_bytes(payload[:4], byteorder="big"),
                int.from_bytes(payload[4:8], byteorder="big"),
                payload[8:],
            )
        raise ValueError(f"Unknown message ID: {msg_id}")

    def _connection_target(self, arguments: list[str]) -> tuple[str, int]:
        if len(arguments) == 1:
            try:
                peer_number = int(arguments[0])
            except ValueError as error:
                raise ValueError("Usage: connect <peer-number> | connect <host> <port>") from error
            if not 1 <= peer_number <= len(self.peers):
                raise ValueError("Invalid peer number. Use 'peers' to view the list.")
            return self.peers[peer_number - 1]
        if len(arguments) == 2:
            return arguments[0], parse_port(arguments[1])
        raise ValueError("Usage: connect <peer-number> | connect <host> <port>")

    def do_status(self, argument: str) -> None:
        """status

        Display the scanned torrent, cached peer count, and connection state.
        """
        if argument.strip():
            self.stdout.write("Usage: status\n")
            return
        torrent_name = str(self.torrent_file) if self.torrent_file else "none"
        connection = (
            f"connected to {self.connected_peer[0]}:{self.connected_peer[1]}"
            if self.connected_peer else "disconnected"
        )
        self.stdout.write(
            f"Torrent: {torrent_name}\nPeers in memory: {len(self.peers)}\nConnection: {connection}\n"
        )

    def do_disconnect(self, argument: str) -> None:
        """disconnect

        Close the current peer connection.
        """
        if argument.strip():
            self.stdout.write("Usage: disconnect\n")
            return
        if not self.connection:
            self.stdout.write("No peer is connected.\n")
            return
        self._disconnect_current()
        self.stdout.write("Peer disconnected.\n")

    def _disconnect_current(self) -> None:
        if self.connection:
            try:
                self.connection.disconnect()
            except OSError:
                pass
        self.connection = None
        self.connected_peer = None
        self.handshake_complete = False

    def do_quit(self, argument: str) -> bool:
        """quit

        Close the client.
        """
        if argument.strip():
            self.stdout.write("Usage: quit\n")
            return False
        self._disconnect_current()
        self.stdout.write("Goodbye.\n")
        return True

    do_exit = do_quit

    def do_EOF(self, argument: str) -> bool:
        """Exit the client with Ctrl-D."""
        self.stdout.write("\n")
        return self.do_quit(argument)


def main() -> int:
    BitTorrentShell().cmdloop()
    return 0


if __name__ == "__main__":
    main()
