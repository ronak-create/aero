#!/usr/bin/env python3
"""Run the test suite without any third-party runner.

    python run_tests.py            # everything, terse output
    python run_tests.py -v         # per-test output

Equivalent to `python -m pytest tests/` for anyone who has pytest, but the
project depends only on the standard library, so this keeps verification
dependency-free.
"""
import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))


def main() -> int:
    loader = unittest.TestLoader()
    # discover() puts the tests directory on sys.path itself; the source root
    # was added above so the bare-name imports (agent, tui, ...) resolve.
    suite = loader.discover(start_dir=str(_HERE / "tests"))
    verbosity = 2 if "-v" in sys.argv else 1
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
