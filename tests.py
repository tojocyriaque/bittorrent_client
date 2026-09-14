"""Test-suite launcher.

Usage:
    python tests.py unit
    python tests.py e2e
    python tests.py all
"""

from pathlib import Path
import sys
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

    def _color(self, text, code):
        return f"\033[{code}m{text}\033[0m" if self.colors_enabled else text

    def startTest(self, test):
        super().startTest(test)
        _, class_name, method_name = test.id().rsplit(".", 2)
        label = (
            f"{class_name.removeprefix('Test')} · "
            f"{method_name.removeprefix('test_').replace('_', ' ')}"
        )
        self.stream.write(f"  {label:<88}")
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


def load_suite(groups):
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for group in groups:
        suite.addTests(loader.discover(TEST_DIRECTORIES[group], "test_*.py"))
    return suite


if __name__ == "__main__":
    requested_group = sys.argv[1] if len(sys.argv) == 2 else "all"
    if requested_group not in {"unit", "e2e", "all"}:
        raise SystemExit("Usage: python tests.py [unit|e2e|all]")

    groups = tuple(TEST_DIRECTORIES) if requested_group == "all" else (requested_group,)
    print(f"\nBittorrent Client — {requested_group.upper()} tests")
    print("=" * 42)
    result = PrettyTestRunner(stream=sys.stdout, verbosity=0).run(load_suite(groups))
    status = "ALL TESTS PASSED" if result.wasSuccessful() else "TESTS FAILED"
    print(f"\n{result.testsRun} tests run — {status}")
    raise SystemExit(not result.wasSuccessful())
