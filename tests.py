"""Tests unitaires du bencoding et de la lecture des fichiers .torrent.

Les cas fonctionnels sont décrits dans tests/bencoding.json et
tests/metainfo.json afin de pouvoir en ajouter sans modifier ce fichier.
"""

import hashlib
import json
from pathlib import Path
import sys
import unittest

import bencoding
import torrent


ROOT = Path(__file__).parent
BENCODING_TESTS = ROOT / "tests" / "bencoding.json"
META_INFO_TESTS = ROOT / "tests" / "metainfo.json"
TORRENTS_DIR = ROOT / "torrents"


class PrettyTestResult(unittest.TextTestResult):
    """Display one concise status line for each test."""

    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        self.colors_enabled = stream.isatty()

    def _color(self, text, code):
        return f"\033[{code}m{text}\033[0m" if self.colors_enabled else text

    @staticmethod
    def _label(test):
        class_name = test.__class__.__name__.removeprefix("Test")
        method_name = test._testMethodName.removeprefix("test_").replace("_", " ")
        return f"{class_name} · {method_name}"

    def startTest(self, test):
        super().startTest(test)
        self.stream.write(f"  {self._label(test):<75}")
        self.stream.flush()

    def addSuccess(self, test):
        super().addSuccess(test)
        self.stream.writeln(self._color("✓ PASS", "32"))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.stream.writeln(self._color("✗ FAIL", "31"))

    def addError(self, test, err):
        super().addError(test, err)
        self.stream.writeln(self._color("✗ ERROR", "31"))


class PrettyTestRunner(unittest.TextTestRunner):
    resultclass = PrettyTestResult

    def run(self, test):
        self.stream.writeln("\nBittorrent Client — Unit Tests")
        self.stream.writeln("=" * 34)
        result = super().run(test)
        status = "ALL TESTS PASSED" if result.wasSuccessful() else "TESTS FAILED"
        color = "32" if result.wasSuccessful() else "31"
        self.stream.writeln(
            f"\n{result.testsRun} tests run — {result._color(status, color)}"
        )
        return result


def json_to_bencoded_value(value):
    """Convertit les chaînes JSON vers le type bytes produit par le décodeur."""
    if isinstance(value, str):
        return value.encode()
    if isinstance(value, list):
        return [json_to_bencoded_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key.encode(): json_to_bencoded_value(item)
            for key, item in value.items()
        }
    return value


class TestBencoding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with BENCODING_TESTS.open(encoding="utf-8") as test_file:
            cls.cases = json.load(test_file)

    def test_encoding_from_json_cases(self):
        for name, case in self.cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    bencoding.bencode_data(case["input"]),
                    case["expected"].encode(),
                )

    def test_decoding_from_json_cases(self):
        for name, case in self.cases.items():
            with self.subTest(name=name):
                encoded = case["expected"].encode()
                decoded, next_index = bencoding.bdecode_bytes(encoded)

                self.assertEqual(decoded, json_to_bencoded_value(case["input"]))
                self.assertEqual(next_index, len(encoded))

    def test_decoding_from_an_offset(self):
        decoded, next_index = bencoding.bdecode_bytes(b"xxx4:spam", 3)

        self.assertEqual(decoded, b"spam")
        self.assertEqual(next_index, 9)

    def test_unsupported_type(self):
        with self.assertRaisesRegex(Exception, "Unsupported data type"):
            bencoding.bencode_data(1.5)


class TestTorrentMetainfo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with META_INFO_TESTS.open(encoding="utf-8") as test_file:
            cls.cases = json.load(test_file)

    def test_reading_torrent_files_from_json_cases(self):
        for name, case in self.cases.items():
            with self.subTest(name=name):
                torrent_path = TORRENTS_DIR / case["input_file"]
                metainfo, next_index = torrent.get_torrent_metainfo(torrent_path)

                self.assertEqual(type(metainfo).__name__, case["expected_type"])
                self.assertEqual(next_index, torrent_path.stat().st_size)
                self.assertEqual(
                    {key.decode() for key in metainfo},
                    set(case["expected_keys"]),
                )
                self.assertEqual(
                    metainfo[b"announce"], case["expected_announce"].encode()
                )
                self.assertEqual(
                    metainfo[b"comment"], case["expected_comment"].encode()
                )
                self.assertEqual(
                    metainfo[b"created by"], case["expected_created_by"].encode()
                )
                self.assertEqual(
                    metainfo[b"creation date"], case["expected_creation_date"]
                )
                self.assertEqual(
                    [url.decode() for url in metainfo[b"url-list"]],
                    case["expected_url_list"],
                )

                info = metainfo[b"info"]
                self.assertIsInstance(info, dict)
                self.assertEqual(
                    {key.decode() for key in info},
                    set(case["expected_info_keys"]),
                )
                self.assertEqual(info[b"name"], case["expected_name"].encode())
                self.assertEqual(info[b"length"], case["expected_length"])
                self.assertEqual(info[b"piece length"], case["expected_piece_length"])
                self.assertEqual(len(info[b"pieces"]), case["expected_pieces_length"])
                self.assertEqual(
                    len(info[b"pieces"]) // 20, case["expected_piece_count"]
                )
                self.assertEqual(len(info[b"pieces"]) % 20, 0)
                self.assertEqual(
                    hashlib.sha1(info[b"pieces"]).hexdigest(),
                    case["expected_pieces_sha1"],
                )

    def test_missing_torrent_file_raises_file_not_found_error(self):
        missing_path = TORRENTS_DIR / "missing-file.torrent"

        with self.assertRaises(FileNotFoundError):
            torrent.get_torrent_metainfo(missing_path)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = PrettyTestRunner(verbosity=0).run(suite)
    raise SystemExit(not result.wasSuccessful())
