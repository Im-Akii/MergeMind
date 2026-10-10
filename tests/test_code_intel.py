"""M1: signals, symbols, lexical search, localization and impact on a tiny git repo."""

import subprocess
from pathlib import Path

import pytest

from mergemind.code import symbols as sym
from mergemind.code.graph import LexicalIntel, co_changed, is_test, query_terms
from mergemind.code.history import pr_number, region_commits
from mergemind.code.localize import difficulty, localize
from mergemind.code.repo_cache import GitError, git, redact
from mergemind.code.signals import extract

AUTH = '''\
import time


class TokenStore:
    def __init__(self):
        self.data = {}

    def refresh_token(self, session):
        """Refresh an expired session token."""
        if session.expired:
            return self.data["token"]
        return session.token


def login(user):
    store = TokenStore()
    return store.refresh_token(user.session)
'''

API = '''\
from app.auth import login


def handle_request(req):
    return login(req.user)
'''

TEST = '''\
from app.auth import TokenStore


def test_refresh_token():
    assert TokenStore().refresh_token
'''

JS = '''\
export function parseConfig(text) {
  if (!text) {
    return {};
  }
  return JSON.parse(text);
}

const helper = (x) => x + 1;
'''


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "app").mkdir(parents=True)
    (r / "tests").mkdir()
    (r / "web").mkdir()
    (r / "app" / "__init__.py").write_text("")
    (r / "app" / "auth.py").write_text(AUTH)
    (r / "tests" / "test_auth.py").write_text(TEST)
    (r / "web" / "config.js").write_text(JS)
    _git(r, "init", "-q")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@e", "add", ".")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "init (#12)")
    (r / "app" / "api.py").write_text(API)
    (r / "app" / "auth.py").write_text(AUTH.replace("Refresh an expired", "Refresh any expired"))
    _git(r, "-c", "user.name=t", "-c", "user.email=t@e", "add", ".")
    _git(r, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "add api")
    return r


TRACE = '''Login fails after the session expires.

Traceback (most recent call last):
  File "/usr/lib/python3.12/site-packages/app/api.py", line 5, in handle_request
    return login(req.user)
  File "/usr/lib/python3.12/site-packages/app/auth.py", line 12, in refresh_token
    return self.data["token"]
KeyError: 'token'

Using version 2.3.1, see #45. The `TokenStore.refresh_token` method looks wrong.
'''


def test_signals_map_trace_into_repo():
    files = {"app/auth.py", "app/api.py", "tests/test_auth.py"}
    sig = extract(TRACE, files)
    assert sig.has_trace and sig.exception == "KeyError"
    # innermost frame first
    assert [f.file for f in sig.frames] == ["app/auth.py", "app/api.py"]
    assert sig.error_signature == "KeyError|app/auth.py:refresh_token|app/api.py:handle_request"
    assert "TokenStore.refresh_token" in sig.identifiers
    assert sig.paths[:2] == ["app/auth.py", "app/api.py"]  # innermost frame first
    assert "2.3.1" in sig.versions and sig.issue_refs == [45]


def test_python_symbols(repo):
    names = {s.name: s for s in sym.symbols(repo / "app" / "auth.py")}
    assert names["TokenStore.refresh_token"].start == 8
    assert names["TokenStore.refresh_token"].end == 12
    assert sym.enclosing(repo / "app" / "auth.py", 11).name == "TokenStore.refresh_token"


def test_js_symbols(repo):
    names = {s.name: s for s in sym.symbols(repo / "web" / "config.js")}
    assert names["parseConfig"].start == 1 and names["parseConfig"].end == 6
    assert "helper" in names


def test_query_terms_drop_prose():
    terms = query_terms("its refresh_token see the TokenStore when it fails")
    assert terms[:2] == ["refresh_token", "TokenStore"]
    assert "its" not in terms and "see" not in terms and "token" in terms


def test_lexical_search_and_definition(repo):
    intel = LexicalIntel(repo)
    top = intel.search("refresh_token KeyError", top_k=3)[0]
    assert (top.file, top.symbol) == ("app/auth.py", "TokenStore.refresh_token")
    d = intel.definition("TokenStore.refresh_token")[0]
    assert d.file == "app/auth.py" and d.start == 8


def test_impact_finds_callers_tests_and_cochange(repo):
    imp = LexicalIntel(repo).impact("login", "app/auth.py")
    assert ("app/api.py", "handle_request") in {(c.file, c.symbol) for c in imp.callers}
    assert dict(co_changed(repo, "app/auth.py"))["app/api.py"] == 1
    imp2 = LexicalIntel(repo).impact("TokenStore.refresh_token", "app/auth.py")
    assert "tests/test_auth.py" in imp2.tests


def test_localize_end_to_end(repo):
    files = set(git(repo, "ls-files").splitlines())
    sig = extract(TRACE, files)
    loc = localize(LexicalIntel(repo), repo, "Login fails after session expires", TRACE, sig, sha="abc")
    top = loc.regions[0]
    assert (top.file, top.symbol) == ("app/auth.py", "TokenStore.refresh_token")
    assert loc.confidence == "high"
    assert "app/auth.py" in loc.scope_files and "tests/test_auth.py" in loc.scope_files
    assert all(e.id.startswith("ev_") for e in loc.evidence)
    assert any(e.kind == "impact" for e in loc.evidence)
    assert difficulty(loc) in ("easy", "medium", "hard")


def test_history(repo):
    commits = region_commits(repo, "app/auth.py", 8, 12)
    assert [c.subject for c in commits] == ["add api", "init (#12)"]
    assert pr_number("init (#12)") == 12 and pr_number("Merge pull request #7 from x/y") == 7


def test_is_test_paths():
    assert is_test("tests/test_auth.py") and is_test("src/foo.test.ts") and is_test("pkg/x_test.go")
    assert not is_test("src/attestation.py")


def test_git_errors_never_leak_tokens(tmp_path):
    secret = "github_pat_" + "A1b2C3d4E5" * 4
    assert secret not in redact(f"fatal: header Authorization: Bearer {secret}")
    assert "[REDACTED]" in redact(f"token {secret}")
    with pytest.raises(GitError) as exc:
        git(tmp_path, "-c", f"http.extraHeader=Authorization: Bearer {secret}", "log")
    assert secret not in str(exc.value)
