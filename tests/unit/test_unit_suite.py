"""Unit tests: all external boundaries are mocked or exercised in isolation."""

import hashlib
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import mock_open, patch
from urllib.parse import unquote_to_bytes, urlsplit


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import bencoding
import torrent


BENCODING_TESTS = ROOT / "tests" / "bencoding.json"


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
    def test_missing_file_raises_file_not_found_error(self):
        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            self.assertRaises(FileNotFoundError),
        ):
            torrent.get_torrent_metainfo("missing-file.torrent")

    def test_reads_bytes_and_returns_decoded_dictionary(self):
        encoded = b"d4:infodee"
        decoded = {b"info": {}}
        with (
            patch("builtins.open", mock_open(read_data=encoded)) as open_file,
            patch("torrent.bencoding.bdecode_bytes", return_value=(decoded, len(encoded))) as decode,
        ):
            self.assertIs(torrent.get_torrent_metainfo("fixture.torrent"), decoded)
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

    def test_build_get_request_contains_complete_tracker_parameters(self):
        with patch("torrent.os.urandom", return_value=b"abcdefghijkl") as random_bytes:
            request_url = torrent.build_get_request(self.metainfo)
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
        output = StringIO()
        with redirect_stdout(output):
            response = torrent.contact_peers(request_url)
        mock_get.assert_called_once_with(request_url)
        self.assertEqual(response, {b"interval": 1800, b"peers": compact_peers})
        self.assertEqual(output.getvalue(), f"Contacting peers...\nrequest: {request_url}\n")

    def test_get_peers_from_response_decodes_all_hosts_and_ports(self):
        response = {b"peers": b"\x7f\x00\x00\x01\x1a\xe1\xc0\xa8\x01\x05\xcb\xd5"}
        self.assertEqual(torrent.get_peers_from_response(response), [("127.0.0.1", 6881), ("192.168.1.5", 52181)])

    def test_get_peers_from_response_handles_no_peers(self):
        self.assertEqual(torrent.get_peers_from_response({b"peers": b""}), [])

    @patch("torrent.get_peers_from_response", return_value=[("10.0.0.2", 51413)])
    @patch("torrent.contact_peers", return_value={b"peers": b"ignored"})
    @patch("torrent.build_get_request", return_value="http://tracker.example/announce")
    @patch("torrent.get_torrent_metainfo", return_value={b"info": {}})
    def test_contact_peers_from_torrent_orchestrates_dependencies(self, metainfo, request, contact, peers):
        output = StringIO()
        with redirect_stdout(output):
            result = torrent.contact_peers_from_torrent("example.torrent")
        metainfo.assert_called_once_with("example.torrent")
        request.assert_called_once_with(metainfo.return_value)
        contact.assert_called_once_with(request.return_value)
        peers.assert_called_once_with(contact.return_value)
        self.assertIsNone(result)
        self.assertEqual(output.getvalue(), "peers:\nPeer 0 : Host 10.0.0.2 Port 51413\n")
