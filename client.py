"""Interactive BitTorrent client console.

Start it with ``python client.py`` and use ``help`` to discover commands.
"""

import cmd
import shlex
import socket
from pathlib import Path
from typing import TextIO

import torrent


PROMPT = "[bittorrent]# "


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
        "or 'scan <torrent-file>' to get started."
    )

    def __init__(self, stdout: TextIO | None = None) -> None:
        super().__init__(stdout=stdout)
        self.peers: list[tuple[str, int]] = []
        self.torrent_file: Path | None = None
        self.connection: torrent.TorrentClient | None = None
        self.connected_peer: tuple[str, int] | None = None

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
            scanner = torrent.TorrentClient()
            try:
                peers = scanner.contact_peers_from_torrent(str(torrent_file))
            finally:
                scanner.disconnect()
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

        self._disconnect_current()
        self.stdout.write(f"Connecting to {host}:{port}...\n")
        client = torrent.TorrentClient()
        try:
            client.connect_to_peer(host, port, debug=False)
        except (socket.gaierror, OSError) as error:
            self.stdout.write(f"Connection failed: {error}\n")
            return

        self.connection = client
        self.connected_peer = (host, port)
        self.stdout.write(f"Connected to {host}:{port}.\n")

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
