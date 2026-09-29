"""
tests/e2e/harness_probe.py

A scenario that fails on purpose, run only by ``test_harness.py`` in a child
pytest. The name has no ``test_`` prefix, so no ordinary run collects it; the
child names the file on its command line, which pytest always collects.

The page throws, the scenario itself asserts nothing, so the failure comes
from the ``page`` fixture's teardown — the failure class the harness exists to
catch, and the one whose trace was being thrown away.
"""

from __future__ import annotations


def test_a_page_that_throws(page):
    page.set_content("<script>throw new Error('harness probe')</script>")
