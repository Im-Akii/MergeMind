"""M7 eval plumbing: metrics, CIs, split, revert detection, economics, report, prompts."""

import subprocess

from mergemind.evals import economics, metrics, prcheck, report
from mergemind.evals.backtest import Row


def row(n, created, truth, ranked, scope=None, tests=None, truth_tests=None, repo="o/r"):
    r = Row(repo, n, created, "sha", truth, truth_tests or [])
    r.ranked = ranked
    r.scope = scope or {}
    r.tests = tests or {}
    return r


def test_hit_recall_mrr():
    r = row(1, "2026-01-01", ["a.py", "b.py"], {"entire": ["x.py", "a.py", "y.py", "b.py"]})
    assert metrics.hit_at(r, "entire", 1) == 0.0
    assert metrics.hit_at(r, "entire", 3) == 1.0
    assert metrics.recall_at(r, "entire", 3) == 0.5
    assert metrics.recall_at(r, "entire", 5) == 1.0
    assert metrics.mrr(r, "entire") == 0.5


def test_split_is_by_time_per_repo():
    rows = [row(i, f"2026-01-{i:02d}", ["a.py"], {}) for i in range(1, 11)]
    dev, test = metrics.split(rows)
    assert len(dev) == 6 and len(test) == 4
    assert max(r.created_at for r in dev) < min(r.created_at for r in test)


def test_bootstrap_ci_brackets_mean():
    mean, lo, hi = metrics.bootstrap([0, 1] * 50)
    assert lo <= mean <= hi and abs(mean - 0.5) < 1e-9 and hi - lo < 0.3


def test_summarize_and_report():
    rows = [row(i, f"2026-01-{i:02d}", ["a.py"], {"entire": ["a.py"], "recency": ["z.py"]},
                scope={"entire": ["a.py", "t.py"]}, tests={"entire": ["tests/test_a.py"]},
                truth_tests=["tests/test_a.py"]) for i in range(1, 11)]
    s = metrics.summarize(rows, ["entire", "recency"])
    assert s["entire"]["Hit@1"][0] == 1.0 and s["recency"]["Hit@5"][0] == 0.0
    assert s["entire"]["Scope coverage"][0] == 1.0 and s["entire"]["Test recall"][0] == 1.0
    md, data = report.locate_report(rows, "t", ["o/r"])
    assert "MergeMind (entire-graph)" in md and data["n_usable"] == 10


def test_reverted_prs_from_history(tmp_path):
    g = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", *a], cwd=tmp_path,  # noqa: E731
                                  check=True, capture_output=True, text=True).stdout.strip()
    g("init", "-q", "-b", "main")
    (tmp_path / "a").write_text("1")
    g("add", ".")
    g("commit", "-qm", "Fix parser (#12)")
    bad = g("rev-parse", "HEAD")
    g("revert", "--no-edit", "HEAD")
    g("commit", "--allow-empty", "-qm", 'Revert "Speed up client (#40)" (#41)')
    g("update-ref", "refs/remotes/origin/main", "HEAD")
    shas, prs = prcheck.reverted_prs(tmp_path, "main")
    assert bad in shas and 40 in prs


def test_cost_of_uses_per_model_prices():
    c = economics.cost_of({"nvidia/nemotron-3-super-120b-a12b": [1_000_000, 100_000],
                           "nvidia/nemotron-3.5-lightning-30b-a3b": [2_000_000, 0]})
    assert abs(c - (0.10 + 0.05 + 0.10)) < 1e-9


def test_economics_report(tmp_path):
    rows = [{"disposition": "accept", "in_tok": 40000, "out_tok": 4000,
             "by_model": {"nvidia/nemotron-3-super-120b-a12b": [38000, 3500],
                          "nvidia/nemotron-3.5-lightning-30b-a3b": [2000, 500]}},
            {"disposition": "needs_info", "in_tok": 20000, "out_tok": 2000,
             "by_model": {"nvidia/nemotron-3-super-120b-a12b": [20000, 2000]}}]
    md = economics.build(rows, tmp_path / "none.db", "nvidia/nemotron-3-super-120b-a12b", tmp_path)
    assert "All-in LLM cost per issue" in md
    assert (tmp_path / "cfo_drivers.csv").read_text().startswith("driver,value,unit,source")


def test_prompt_registry_falls_back_to_local():
    from mergemind import prompts

    text, version = prompts.get("investigator_test_only", "LOCAL TEXT")
    assert text == "LOCAL TEXT" and version.startswith("local-")
    assert set(prompts.registry()) == {"investigator", "critic", "dup_adjudicator", "pr_reviewer", "leak_critic",
                                       "answerer"}
