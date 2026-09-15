"""Unit tests: all external boundaries are mocked or exercised in isolation."""

from contextlib import nullcontext
import hashlib
import json
from io import StringIO
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, mock_open, patch
from urllib.parse import unquote_to_bytes, urlsplit


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import bencoding
import client
import torrent


BENCODING_TESTS = ROOT / "tests" / "bencoding.json"


def diagnostic_output():
    """Keep normal runs quiet while exposing diagnostics with ``--debug``."""
    if os.environ.get("BITTORRENT_TEST_DEBUG") == "1":
        return nullcontext()
    return patch("builtins.print")


def json_to_bencoded_value(value):
    if isinstance(value, str):
        return value.encode()
    if isinstance(value, list):
        return [json_to_bencoded_value(item) for item in value]
    if isinstance(value, dict):
        return {key.encode(): json_to_bencoded_value(item) for key, item in value.items()}
    return value


class TestBencoding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with BENCODING_TESTS.open(encoding="utf-8") as test_file:
            cls.cases = json.load(test_file)

    def test_encoding_matches_json_cases(self):
        for name, case in self.cases.items():
            with self.subTest(name=name):
                self.assertEqual(bencoding.bencode_data(case["input"]), case["expected"].encode())

    def test_decoding_matches_json_cases_and_consumes_input(self):
        for name, case in self.cases.items():
            with self.subTest(name=name):
                encoded = case["expected"].encode()
                decoded, next_index = bencoding.bdecode_bytes(encoded)
                self.assertEqual(decoded, json_to_bencoded_value(case["input"]))
                self.assertEqual(next_index, len(encoded))

    def test_decoding_honors_start_offset(self):
        self.assertEqual(bencoding.bdecode_bytes(b"xxx4:spam", 3), (b"spam", 9))

    def test_round_trip_preserves_binary_bytes_and_dictionary_order(self):
        value = {b"z": b"\x00\xff", b"a": [b"\x00", -1]}
        encoded = bencoding.bencode_data(value)
        self.assertEqual(encoded, b"d1:al1:\x00i-1ee1:z2:\x00\xffe")
        self.assertEqual(bencoding.bdecode_bytes(encoded), (value, len(encoded)))

    def test_unsupported_type_raises(self):
        with self.assertRaisesRegex(Exception, "Unsupported data type"):
            bencoding.bencode_data(1.5)


class TestTorrentMetainfo(unittest.TestCase):
    def setUp(self):
        self.socket_patcher = patch("torrent.socket.socket")
        self.socket_patcher.start()
        self.client = torrent.TorrentClient()

    def tearDown(self):
        if self.client.socket_conn is not None:
            self.client.socket_conn.close()
        self.socket_patcher.stop()

    def test_missing_file_raises_file_not_found_error(self):
        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            self.assertRaises(FileNotFoundError),
        ):
            self.client.get_torrent_metainfo("missing-file.torrent")

    def test_reads_bytes_and_returns_decoded_dictionary(self):
        encoded = b"d4:infodee"
        decoded = {b"info": {}}
        with (
            patch("builtins.open", mock_open(read_data=encoded)) as open_file,
            patch("torrent.bencoding.bdecode_bytes", return_value=(decoded, len(encoded))) as decode,
        ):
            self.assertIs(self.client.get_torrent_metainfo("fixture.torrent"), decoded)
        open_file.assert_called_once_with("fixture.torrent", "rb")
        decode.assert_called_once_with(encoded)


class TestTrackerUnits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metainfo = {
            b"announce": b"http://bttracker.debian.org:6969/announce",
            b"info": {
                b"length": 792723456,
                b"name": b"debian.iso",
                b"piece length": 262144,
                b"pieces": b"",
            },
        }

    @staticmethod
    def query_parameters(request_url):
        parsed = urlsplit(request_url)
        return parsed, {key: unquote_to_bytes(value) for key, value in (item.split("=", 1) for item in parsed.query.split("&"))}

    def setUp(self):
        self.socket_patcher = patch("torrent.socket.socket")
        self.socket_patcher.start()
        self.client = torrent.TorrentClient()

    def tearDown(self):
        if self.client.socket_conn is not None:
            self.client.socket_conn.close()
        self.socket_patcher.stop()

    def test_build_get_request_contains_complete_tracker_parameters(self):
        with patch("torrent.os.urandom", return_value=b"abcdefghijkl") as random_bytes:
            request_url = self.client.build_get_request(self.metainfo)
        parsed, parameters = self.query_parameters(request_url)
        random_bytes.assert_called_once_with(12)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), ("http", "bttracker.debian.org:6969", "/announce"))
        self.assertEqual(parameters, {
            "info_hash": hashlib.sha1(bencoding.bencode_data(self.metainfo[b"info"])).digest(),
            "peer_id": b"-PY0001-abcdefghijkl", "port": b"6882", "uploaded": b"0",
            "downloaded": b"0", "left": b"792723456", "compact": b"1",
        })

    @patch("torrent.requests.get")
    def test_contact_peers_decodes_tracker_body(self, mock_get):
        request_url = "http://tracker.example/announce?compact=1"
        compact_peers = b"\x7f\x00\x00\x01\x1a\xe1\xc0\xa8\x01\x05\xcb\xd5"
        mock_get.return_value.content = b"d8:intervali1800e5:peers12:" + compact_peers + b"e"
        with diagnostic_output():
            response = self.client.contact_peers(request_url)
        mock_get.assert_called_once_with(request_url)
        self.assertEqual(response, {b"interval": 1800, b"peers": compact_peers})

    def test_contact_peers_from_torrent_orchestrates_dependencies_and_returns_peers(
        self,
    ):
        self.client.get_torrent_metainfo = Mock(return_value={b"info": {}})
        self.client.build_get_request = Mock(return_value="http://tracker.example/announce")
        self.client.contact_peers = Mock(return_value={b"peers": b"\x0a\x00\x00\x02\xc8\xd5"})
        result = self.client.contact_peers_from_torrent("example.torrent")
        self.client.get_torrent_metainfo.assert_called_once_with("example.torrent")
        self.client.build_get_request.assert_called_once_with(
            self.client.get_torrent_metainfo.return_value
        )
        self.client.contact_peers.assert_called_once_with(self.client.build_get_request.return_value)
        self.assertEqual(result, [("10.0.0.2", 51413)])

    def test_contact_peers_from_torrent_returns_peers_in_debug_mode(
        self,
    ):
        self.client.get_torrent_metainfo = Mock(return_value={b"info": {}})
        self.client.build_get_request = Mock(return_value="http://tracker.example/announce")
        self.client.contact_peers = Mock(return_value={b"peers": b"\x0a\x00\x00\x02\xc8\xd5"})
        with diagnostic_output():
            result = self.client.contact_peers_from_torrent("example.torrent", debug=True)

        self.assertEqual(result, [("10.0.0.2", 51413)])


class TestInteractiveClient(unittest.TestCase):
    def setUp(self):
        self.output = StringIO()
        self.shell = client.BitTorrentShell(stdout=self.output)

    def test_scan_caches_peers_and_peers_lists_them(self):
        self.shell.client = Mock()
        self.shell.client.contact_peers_from_torrent.return_value = [
            ("127.0.0.1", 6881),
            ("10.0.0.2", 51413),
        ]
        with (
            patch("client.validate_torrent_path", return_value=Path("sample.torrent")),
        ):
            self.shell.onecmd("scan sample.torrent")
        self.shell.onecmd("peers")

        self.shell.client.contact_peers_from_torrent.assert_called_once_with("sample.torrent")
        self.assertEqual(self.shell.peers, [("127.0.0.1", 6881), ("10.0.0.2", 51413)])
        self.assertEqual(
            self.output.getvalue(),
            "Scan complete: 2 peer(s) found.\n"
            "Use 'peers' to list them or 'connect <number>'.\n"
            "#  HOST       PORT\n"
            "-  ---------  -----\n"
            "1  127.0.0.1  6881\n"
            "2  10.0.0.2   51413\n",
        )

    def test_scan_and_connect_share_one_client_instance(self):
        shared_client = Mock()
        shared_client.contact_peers_from_torrent.return_value = [("10.0.0.2", 51413)]
        output = StringIO()
        with (
            patch("client.torrent.TorrentClient", return_value=shared_client) as client_factory,
            patch("client.validate_torrent_path", return_value=Path("sample.torrent")),
        ):
            shell = client.BitTorrentShell(stdout=output)
            shell.onecmd("scan sample.torrent")
            shell.onecmd("connect 1")
            shared_client.recv_msg.return_value = None
            shell.onecmd("handshake")

        client_factory.assert_called_once_with()
        shared_client.contact_peers_from_torrent.assert_called_once_with("sample.torrent")
        shared_client.connect_to_peer.assert_called_once_with("10.0.0.2", 51413, debug=False)
        shared_client.send_handshake.assert_called_once_with()
        shared_client.verify_info.assert_called_once()

    def test_connect_accepts_a_cached_peer_number(self):
        self.shell.client = Mock()
        self.shell.client.recv_msg.return_value = None
        self.shell.peers = [("10.0.0.2", 51413)]
        self.shell.torrent_file = Path("sample.torrent")
        self.shell.onecmd("connect 1")
        self.shell.onecmd("handshake")

        self.shell.client.connect_to_peer.assert_called_once_with("10.0.0.2", 51413, debug=False)
        self.shell.client.send_handshake.assert_called_once_with()
        self.shell.client.verify_info.assert_called_once()
        self.assertIs(self.shell.connection, self.shell.client)
        self.assertEqual(self.shell.connected_peer, ("10.0.0.2", 51413))
        self.assertEqual(
            self.output.getvalue(),
            "Connecting to 10.0.0.2:51413...\nConnected to 10.0.0.2:51413.\n"
            "Establishing BitTorrent handshake...\nHandshake complete.\n",
        )

    def test_connect_rejects_an_invalid_port_before_opening_a_socket(self):
        self.shell.client = Mock()
        self.shell.onecmd("connect example.com 70000")

        self.shell.client.connect_to_peer.assert_not_called()
        self.assertEqual(self.output.getvalue(), "Port must be between 1 and 65535.\n")

    def test_send_and_receive_messages_after_handshake(self):
        self.shell.client = Mock()
        self.shell.connection = self.shell.client
        self.shell.connection.socket_conn = Mock()
        self.shell.handshake_complete = True
        self.shell.client.recv_msg.return_value = (4, b"\x00\x00\x00\x07")
        self.shell.client.decode_payload.return_value = 7

        self.shell.onecmd("send interested")
        self.shell.onecmd("receive")

        self.shell.client.send_msg.assert_called_once_with(2, None)
        self.shell.client.recv_msg.assert_called_once_with()
        self.shell.client.decode_payload.assert_called_once_with(4, b"\x00\x00\x00\x07")
        self.assertEqual(self.output.getvalue(), "Sent interested.\nReceived have: 7\n")

    def test_peer_messages_require_a_successful_handshake(self):
        self.shell.client = Mock()
        self.shell.connection = self.shell.client

        self.shell.onecmd("send interested")
        self.shell.onecmd("receive")

        self.shell.client.recv_msg.assert_not_called()
        self.assertEqual(
            self.output.getvalue(),
            "Handshake is required. Use 'handshake' first.\n"
            "Handshake is required. Use 'handshake' first.\n",
        )

    def test_send_keepalive_writes_an_empty_peer_wire_frame(self):
        self.shell.client = Mock()
        self.shell.connection = self.shell.client
        self.shell.connection.socket_conn = Mock()
        self.shell.handshake_complete = True

        self.shell.onecmd("send keepalive")

        self.shell.connection.socket_conn.sendall.assert_called_once_with(b"\x00\x00\x00\x00")
        self.assertEqual(self.output.getvalue(), "Sent keep-alive.\n")
