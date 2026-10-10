"""M3: the seeded leak corpus must produce zero leaks and no false redactions."""

import pytest

from mergemind.evals.leak_corpus import (BENIGN_CASES, DB_PASSWORD, GH_TOKEN, LEAK_CASES, MAINTAINER_NOTE, SCOPE,
                                         build_repo, run_corpus)
from mergemind.guard.leak import LeakGuard
from mergemind.guard.secrets import SecretScanner, shannon_entropy

POLICY_SENSITIVE = [".env*", "**/.env*", "**/secrets/**", "deploy/**", "infra/**"]


@pytest.fixture()
def guard(tmp_path):
    repo = build_repo(tmp_path / "planted")
    files = [str(p.relative_to(repo)).replace("\\", "/") for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]
    return LeakGuard.build(repo, files, POLICY_SENSITIVE)


def test_corpus_zero_leaks_and_no_false_redactions(guard):
    outcomes, m = run_corpus(guard)
    failures = [(o.case.name, o.decision, o.leaked, o.text[:120]) for o in outcomes if not o.ok]
    assert not failures, failures
    assert m["leaks"] == 0 and m["false_redactions"] == 0


@pytest.mark.parametrize("case", LEAK_CASES, ids=lambda c: c.name)
def test_each_leak_case_is_not_passed(guard, case):
    res = guard.check(case.message, kind="t", allowed_files=SCOPE, sensitive_excerpts=[MAINTAINER_NOTE],
                      use_critic=False)
    assert res.decision != "passed"


@pytest.mark.parametrize("case", BENIGN_CASES, ids=lambda c: c.name)
def test_each_benign_case_passes_unchanged(guard, case):
    res = guard.check(case.message, kind="t", allowed_files=SCOPE, allowed_mentions=case.allowed_mentions,
                      use_critic=False)
    assert res.decision == "passed" and res.text == case.message


def test_values_learned_from_restricted_files(guard):
    # low-entropy secrets are caught because the file they live in is restricted
    assert DB_PASSWORD in guard.scanner.known


def test_findings_never_contain_secret_values(guard):
    res = guard.check(f"token {GH_TOKEN} and {DB_PASSWORD}", kind="t", use_critic=False)
    assert GH_TOKEN not in str(res.findings) and DB_PASSWORD not in str(res.findings)


def test_git_shas_are_not_secrets():
    s = SecretScanner()
    assert s.scan("fixed in 4f2a9c1e3b7d8a6f5e4d3c2b1a0f9e8d7c6b5a49") == []
    assert shannon_entropy("aaaa") == 0


class _FailingLLM:
    settings = type("S", (), {"llm_model_fast": "m"})()

    def structured(self, *a, **k):
        from mergemind.llm import LLMError

        raise LLMError("server", "down")


def test_critic_outage_fails_closed(guard):
    guard.llm = _FailingLLM()
    res = guard.check("The fix is in app/auth.py.", kind="t", allowed_files=SCOPE)
    assert res.decision == "held_for_maintainer"
