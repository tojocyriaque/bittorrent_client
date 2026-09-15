"""Test-suite launcher.

Usage:
    python tests.py [unit|e2e|all] [--debug]
"""

import argparse
import os
from pathlib import Path
import sys
import time
import unittest


ROOT = Path(__file__).parent
TEST_DIRECTORIES = {
    "unit": ROOT / "tests" / "unit",
    "e2e": ROOT / "tests" / "e2e",
}


class PrettyTestResult(unittest.TextTestResult):
    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        self.colors_enabled = stream.isatty()
        self.debug = False
        self.total_tests = 0
        self.completed_tests = 0
        self._started_at = 0.0

    def _color(self, text, code):
        return f"\033[{code}m{text}\033[0m" if self.colors_enabled else text

    def startTest(self, test):
        super().startTest(test)
        self._started_at = time.perf_counter()
        _, class_name, method_name = test.id().rsplit(".", 2)
        label = (
            f"{class_name.removeprefix('Test')} · "
            f"{method_name.removeprefix('test_').replace('_', ' ')}"
        )
        if self.debug:
            self.stream.write(f"\n  [{self.completed_tests + 1:>2}/{self.total_tests}] {label}\n")
            self.stream.write("       running... ")
        else:
            self.stream.write(f"  {label:<88}")
        self.stream.flush()

    def _result_label(self, label, code):
        self.completed_tests += 1
        elapsed = time.perf_counter() - self._started_at
        suffix = f" ({elapsed:.3f}s)" if self.debug else ""
        self.stream.writeln(self._color(f"{label}{suffix}", code))

    def addSuccess(self, test):
        super().addSuccess(test)
        self._result_label("✓ PASS", "32")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._result_label("✗ FAIL", "31")

    def addError(self, test, err):
        super().addError(test, err)
        self._result_label("✗ ERROR", "31")


class PrettyTestRunner(unittest.TextTestRunner):
    resultclass = PrettyTestResult

    def __init__(self, *args, debug=False, total_tests=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.debug = debug
        self.total_tests = total_tests

    def _makeResult(self):
        result = super()._makeResult()
        result.debug = self.debug
        result.total_tests = self.total_tests
        return result


def load_suite(groups):
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for group in groups:
        suite.addTests(loader.discover(TEST_DIRECTORIES[group], "test_*.py"))
    return suite


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Bittorrent Client test suite.")
    parser.add_argument("group", nargs="?", choices=("unit", "e2e", "all"), default="all")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="show a numbered progress line and duration for every test",
    )
    arguments = parser.parse_args()
    if arguments.debug:
        os.environ["BITTORRENT_TEST_DEBUG"] = "1"
    else:
        os.environ.pop("BITTORRENT_TEST_DEBUG", None)

    groups = tuple(TEST_DIRECTORIES) if arguments.group == "all" else (arguments.group,)
    suite = load_suite(groups)
    total_tests = suite.countTestCases()
    mode = " — DEBUG" if arguments.debug else ""
    print(f"\nBittorrent Client — {arguments.group.upper()} tests{mode}")
    print("=" * 42)
    result = PrettyTestRunner(
        stream=sys.stdout,
        verbosity=0,
        debug=arguments.debug,
        total_tests=total_tests,
    ).run(suite)
    status = "ALL TESTS PASSED" if result.wasSuccessful() else "TESTS FAILED"
    print(f"\n{result.testsRun} tests run — {status}")
    raise SystemExit(not result.wasSuccessful())
