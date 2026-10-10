"""The real entire-graph backend on a tiny repo. Skipped when the Entire CLI or its graph
plugin isn't installed."""

import subprocess

import pytest

from mergemind.code.graph import EntireIntel, LexicalIntel, entire_binary
from tests.test_code_intel import repo  # noqa: F401  (fixture)

BIN = entire_binary()


def _has_graph() -> bool:
    if not BIN:
        return False
    proc = subprocess.run([BIN, "graph", "version"], capture_output=True, text=True)
    return proc.returncode == 0


pytestmark = pytest.mark.skipif(not _has_graph(), reason="entire CLI with the graph plugin is not installed")


@pytest.fixture()
def intel(repo):  # noqa: F811
    return EntireIntel(repo, LexicalIntel(repo), BIN)


def test_search_finds_the_method(intel):
    top = intel.search("refresh_token raises KeyError for an expired session", top_k=5)
    assert top and top[0].backend == "entire"
    assert ("app/auth.py", "TokenStore.refresh_token") in {(r.file, r.symbol) for r in top}


def test_definition_exact_name_only(intel):
    d = intel.definition("refresh_token")
    assert d and d[0].file == "app/auth.py" and d[0].symbol.endswith("refresh_token")
    assert intel.definition("notepad.exe") == []


def test_impact_callers_and_tests(intel):
    imp = intel.impact("login", "app/auth.py")
    assert ("app/api.py", "handle_request") in {(c.file, c.symbol) for c in imp.callers}
    imp2 = intel.impact("TokenStore.refresh_token", "app/auth.py")
    assert "tests/test_auth.py" in imp2.tests
