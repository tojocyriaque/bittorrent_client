"""End-to-end test: torrent file -> local HTTP tracker -> displayed peers."""

from contextlib import redirect_stdout
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.parse import unquote_to_bytes, urlsplit


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import bencoding
import torrent


class TestTrackerFlowEndToEnd(unittest.TestCase):
    def test_real_torrent_file_matches_declared_metainfo_fixture(self):
        fixture_path = ROOT / "tests" / "metainfo.json"
        torrents_directory = ROOT / "torrents"
        with fixture_path.open(encoding="utf-8") as fixture_file:
            cases = json.load(fixture_file)

        for name, case in cases.items():
            with self.subTest(name=name):
                metainfo = torrent.get_torrent_metainfo(
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

    def test_contact_peers_from_torrent_uses_local_tracker_and_displays_peers(self):
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
                output = StringIO()
                with patch("torrent.os.urandom", return_value=b"abcdefghijkl"), redirect_stdout(output):
                    result = torrent.contact_peers_from_torrent(torrent_path)
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join()

        self.assertIsNone(result)
        self.assertEqual(len(received_paths), 1)
        parsed = urlsplit(received_paths[0])
        parameters = {key: unquote_to_bytes(value) for key, value in (item.split("=", 1) for item in parsed.query.split("&"))}
        request_url = f"http://127.0.0.1:{server.server_port}{received_paths[0]}"
        self.assertEqual(parsed.path, "/announce")
        self.assertEqual(parameters["peer_id"], b"-PY0001-abcdefghijkl")
        self.assertEqual(parameters["left"], b"123")
        self.assertEqual(
            output.getvalue(),
            "Contacting peers...\n"
            f"request: {request_url}\n"
            "peers:\n"
            "Peer 0 : Host 127.0.0.1 Port 6881\n"
            "Peer 1 : Host 192.168.1.5 Port 52181\n",
        )
