"""Make the flat top-level modules importable when running the test suite.

The project is deliberately not pip-installed -- cli.py, agent.py, tui.py all
import each other by bare name -- so the source directory has to be on
sys.path before any test imports them.
"""
import pathlib
import sys

_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
