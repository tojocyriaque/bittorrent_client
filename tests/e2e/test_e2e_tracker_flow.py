"""End-to-end test: torrent file -> local HTTP tracker -> displayed peers."""

from contextlib import nullcontext
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.parse import unquote_to_bytes, urlsplit


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import bencoding
import client
import torrent


def debug(message: str) -> None:
    """Show integration-test milestones only when ``tests.py --debug`` is used."""
    if os.environ.get("BITTORRENT_TEST_DEBUG") == "1":
        print(f"       [e2e] {message}", flush=True)


def diagnostic_output():
    """Keep normal runs quiet while exposing diagnostics with ``--debug``."""
    if os.environ.get("BITTORRENT_TEST_DEBUG") == "1":
        return nullcontext()
    return patch("builtins.print")


class TestTrackerFlowEndToEnd(unittest.TestCase):
    def test_real_torrent_file_matches_declared_metainfo_fixture(self):
        fixture_path = ROOT / "tests" / "metainfo.json"
        torrents_directory = ROOT / "torrents"
        with fixture_path.open(encoding="utf-8") as fixture_file:
            cases = json.load(fixture_file)

        torrent_client = torrent.TorrentClient()
        try:
            for name, case in cases.items():
                with self.subTest(name=name):
                    metainfo = torrent_client.get_torrent_metainfo(
                        torrents_directory / case["input_file"]
                    )
                    info = metainfo[b"info"]
                    self.assertEqual({key.decode() for key in metainfo}, set(case["expected_keys"]))
                    self.assertEqual(metainfo[b"announce"], case["expected_announce"].encode())
                    self.assertEqual(metainfo[b"creation date"], case["expected_creation_date"])
                    self.assertEqual(info[b"name"], case["expected_name"].encode())
                    self.assertEqual(info[b"length"], case["expected_length"])
                    self.assertEqual(info[b"piece length"], case["expected_piece_length"])
                    self.assertEqual(len(info[b"pieces"]) // 20, case["expected_piece_count"])
                    self.assertEqual(
                        hashlib.sha1(info[b"pieces"]).hexdigest(),
                        case["expected_pieces_sha1"],
                    )
        finally:
            if torrent_client.socket_conn is not None:
                torrent_client.socket_conn.close()

    def test_contact_peers_from_torrent_uses_local_tracker_and_returns_peers(self):
        compact_peers = b"\x7f\x00\x00\x01\x1a\xe1\xc0\xa8\x01\x05\xcb\xd5"
        received_paths = []

        class LocalTracker(BaseHTTPRequestHandler):
            def do_GET(self):
                received_paths.append(self.path)
                body = b"d8:intervali1800e5:peers12:" + compact_peers + b"e"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), LocalTracker)
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            announce_url = f"http://127.0.0.1:{server.server_port}/announce".encode()
            info = {b"length": 123, b"name": b"sample.bin", b"piece length": 16, b"pieces": b""}
            metainfo = {b"announce": announce_url, b"info": info}
            with TemporaryDirectory() as temporary_directory:
                torrent_path = Path(temporary_directory) / "sample.torrent"
                torrent_path.write_bytes(bencoding.bencode_data(metainfo))
                shell_output = StringIO()
                shell = client.BitTorrentShell(stdout=shell_output)
                with patch("torrent.os.urandom", return_value=b"abcdefghijkl"), diagnostic_output():
                    shell.onecmd(f"scan {torrent_path}")
                shell.onecmd("peers")
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join()

        self.assertEqual(shell.peers, [("127.0.0.1", 6881), ("192.168.1.5", 52181)])
        self.assertEqual(len(received_paths), 1)
        parsed = urlsplit(received_paths[0])
        parameters = {key: unquote_to_bytes(value) for key, value in (item.split("=", 1) for item in parsed.query.split("&"))}
        self.assertEqual(parsed.path, "/announce")
        self.assertEqual(parameters["peer_id"], b"-PY0001-abcdefghijkl")
        self.assertEqual(parameters["left"], b"123")
        self.assertEqual(
            shell_output.getvalue(),
            "Scan complete: 2 peer(s) found.\n"
            "Use 'peers' to list them or 'connect <number>'.\n"
            "#  HOST         PORT\n"
            "-  -----------  -----\n"
            "1  127.0.0.1    6881\n"
            "2  192.168.1.5  52181\n",
        )

    def test_shell_exchanges_peer_wire_messages_with_a_local_peer(self):
        received_frames = []
        peer_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        peer_listener.bind(("127.0.0.1", 0))
        peer_listener.listen(1)
        peer_host, peer_port = peer_listener.getsockname()
        debug(f"peer listening on {peer_host}:{peer_port}")

        def receive_exact(connection, size):
            data = b""
            while len(data) < size:
                chunk = connection.recv(size - len(data))
                if not chunk:
                    raise ConnectionError("Client closed the connection")
                data += chunk
            return data

        def serve_peer():
            try:
                debug("peer waiting for the shell connection")
                connection, _ = peer_listener.accept()
                with connection:
                    debug("peer accepted TCP connection; waiting for handshake")
                    handshake = receive_exact(connection, 68)
                    info_hash = handshake[28:48]
                    peer_id = b"-TEST00-local-peer-1"
                    connection.sendall(
                        b"\x13BitTorrent protocol" + b"\x00" * 8 + info_hash + peer_id
                    )
                    debug("peer returned handshake; waiting for one peer-wire message")
                    length = int.from_bytes(receive_exact(connection, 4), byteorder="big")
                    received_frames.append(receive_exact(connection, length))
                    debug(f"peer received message: {received_frames[-1]!r}")
                    connection.sendall(b"\x00\x00\x00\x05\x04\x00\x00\x00\x07")
                    debug("peer sent 'have 7'")
            finally:
                peer_listener.close()

        peer_thread = Thread(target=serve_peer, daemon=True)
        peer_thread.start()
        compact_peer = socket.inet_aton(peer_host) + peer_port.to_bytes(2, byteorder="big")

        class LocalTracker(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"d5:peers6:" + compact_peer + b"e"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        tracker = ThreadingHTTPServer(("127.0.0.1", 0), LocalTracker)
        tracker_thread = Thread(target=tracker.serve_forever, daemon=True)
        tracker_thread.start()
        debug(f"tracker listening on 127.0.0.1:{tracker.server_port}")
        try:
            announce_url = f"http://127.0.0.1:{tracker.server_port}/announce".encode()
            metainfo = {
                b"announce": announce_url,
                b"info": {b"length": 1, b"name": b"sample.bin", b"piece length": 1, b"pieces": b""},
            }
            with TemporaryDirectory() as temporary_directory:
                torrent_path = Path(temporary_directory) / "sample.torrent"
                torrent_path.write_bytes(bencoding.bencode_data(metainfo))
                output = StringIO()
                shell = client.BitTorrentShell(stdout=output)
                with patch("torrent.os.urandom", return_value=b"abcdefghijkl"):
                    debug("shell scans torrent through local tracker")
                    shell.onecmd(f"scan {torrent_path}")
                debug("shell connects to the peer")
                shell.onecmd("connect 1")
                debug("shell performs handshake")
                shell.onecmd("handshake")
                debug("shell sends 'interested'")
                shell.onecmd("send interested")
                debug("shell waits for the peer response")
                shell.onecmd("receive")
                shell.onecmd("disconnect")
        finally:
            tracker.shutdown()
            tracker.server_close()
            tracker_thread.join()
            peer_thread.join()

        self.assertEqual(received_frames, [b"\x02"])
        self.assertEqual(
            output.getvalue(),
            "Scan complete: 1 peer(s) found.\n"
            "Use 'peers' to list them or 'connect <number>'.\n"
            f"Connecting to {peer_host}:{peer_port}...\n"
            f"Connected to {peer_host}:{peer_port}.\n"
            "Establishing BitTorrent handshake...\n"
            "Handshake complete.\n"
            "Sent interested.\n"
            "Received have: 7\n"
            "Peer disconnected.\n",
        )
