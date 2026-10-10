"""mergemind CLI: run / tick / triage."""

from __future__ import annotations

import json
import logging
import re

import typer

from mergemind import tracing
from mergemind.config import Settings
from mergemind.engine.effects import Context
from mergemind.faults import Faults
from mergemind.github.client import GitHub
from mergemind.store.db import Store

# never print local variables in tracebacks: they can hold API keys
app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_show_locals=False)

_ISSUE_URL = re.compile(r"github\.com/([^/]+/[^/]+)/issues/(\d+)")


def _context(dry_run: bool = False, need_llm: bool = True) -> Context:
    settings = Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    tracing.init(settings.neatlogs_api_key,
                 tags=[f"eval:{settings.eval_run_id}"] if settings.eval_run_id else ["live"])
    store = Store(settings.db_path)
    faults = Faults.parse(settings.faults)
    gh = GitHub(settings.github_token, store=store, faults=faults)
    llm = None
    if need_llm:
        from mergemind.llm import LLM

        llm = LLM(settings, store=store, faults=faults)
    return Context(settings=settings, store=store, gh=gh, llm=llm, dry_run=dry_run)


@app.command()
def triage(
    url: str,
    quick: bool = typer.Option(False, "--quick", help="single LLM call on the issue text only (M0)"),
    fetch: bool = True,
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Triage one issue: code localization, duplicates, investigator, verifier. Never writes to GitHub."""
    from mergemind.config import Policy

    m = _ISSUE_URL.search(url)
    if not m:
        raise typer.BadParameter("expected https://github.com/<owner>/<repo>/issues/<n>")
    repo, number = m.group(1), int(m.group(2))
    ctx = _context(dry_run=True)

    if quick:
        from mergemind.agents.quick import quick_triage
        from mergemind.text import issue_for_model

        issue = ctx.gh.get_issue(repo, number)
        comments = ctx.gh.list_issue_comments(repo, number) if issue.get("comments") else []
        report, usage = quick_triage(ctx.llm, issue_for_model(issue, comments))
        typer.echo(json.dumps(report.model_dump(), indent=2))
        typer.echo(f"\ntokens in={usage.in_tok} out={usage.out_tok} cached={usage.cached} repairs={usage.repairs}")
        tracing.flush()
        return

    from mergemind.triage import triage_issue

    policy = ctx.policy(repo) if ctx.settings.github_token else Policy()
    out = triage_issue(settings=ctx.settings, store=ctx.store, gh=ctx.gh, llm=ctx.llm, policy=policy,
                       repo=repo, number=number, fetch=fetch)
    r = out.report
    if as_json:
        typer.echo(json.dumps({"report": r.model_dump(), "difficulty": out.difficulty, "gate": out.gate,
                               "verifier": None if out.verdict is None else
                               {"ok": out.verdict.ok, "failures": out.verdict.failures},
                               "notes": out.notes, "tool_calls": out.tool_calls, "repairs": out.repairs,
                               "tokens": {"in": out.usage.in_tok, "out": out.usage.out_tok}}, indent=2))
    else:
        typer.echo(f"\n{repo}#{number}: {r.disposition.upper()}  ({r.issue_type}, difficulty {out.difficulty}, "
                   f"confidence {r.disposition_confidence:.2f})")
        typer.echo(f"summary: {r.summary}")
        if out.gate:
            typer.echo(f"decided by fast gate: {out.gate}")
        if r.duplicate_of:
            typer.echo(f"duplicate of #{r.duplicate_of}")
        for reg in r.localization:
            typer.echo(f"  location: {reg.file} L{reg.start}-{reg.end} {reg.symbol or ''} [{reg.role}] ({reg.evidence})")
        for c in r.acceptance_criteria:
            typer.echo(f"  done when: {c}")
        for mi in r.missing_info:
            typer.echo(f"  missing: {mi.field} - {mi.why}")
        for q in r.open_questions:
            typer.echo(f"  open question: {q}")
        for c in r.claims:
            typer.echo(f"  claim: {c.text}  {c.evidence}")
        if r.scope:
            typer.echo("  scope: " + ", ".join(sorted({s.file for s in r.scope})))
        if out.verdict is not None:
            typer.echo(f"verifier: {'pass' if out.verdict.ok else 'FAIL'} {out.verdict.failures or ''}")
        for n in out.notes:
            typer.echo(f"note: {n}")
        typer.echo(f"tool calls={out.tool_calls} repairs={out.repairs} tokens in={out.usage.in_tok} "
                   f"out={out.usage.out_tok} cached={out.usage.cached}")
    tracing.flush()


@app.command()
def locate(url: str, fetch: bool = True, as_json: bool = typer.Option(False, "--json")) -> None:
    """Localize an issue in the code and print regions, impact and scope. Uses no LLM."""
    from mergemind.code.graph import code_intels
    from mergemind.code.history import region_commits
    from mergemind.code.localize import difficulty, localize_fused
    from mergemind.code.repo_cache import RepoCache
    from mergemind.code.signals import extract
    from mergemind.text import clean

    m = _ISSUE_URL.search(url)
    if not m:
        raise typer.BadParameter("expected https://github.com/<owner>/<repo>/issues/<n>")
    repo, number = m.group(1), int(m.group(2))
    ctx = _context(need_llm=False)
    issue = ctx.gh.get_issue(repo, number)
    cache = RepoCache(ctx.settings.work_dir, ctx.settings.github_token)
    private = bool(ctx.gh.get_repo(repo).get("private"))
    path = cache.ensure(repo, fetch=fetch, private=private)
    sha = cache.head(repo)
    cache.checkout_detached(repo, sha)
    title, body = clean(issue.get("title"), 300), clean(issue.get("body"))
    sig = extract(f"{title}\n{body}", set(cache.ls_files(path)))
    loc = localize_fused(code_intels(path), path, title, body, sig, sha=sha)
    for ev in loc.evidence:
        ctx.store.add_evidence(None, ev)

    if as_json:
        typer.echo(json.dumps({
            "repo": repo, "issue": number, "sha": sha, "backend": loc.backend,
            "confidence": loc.confidence, "difficulty": difficulty(loc),
            "signals": {"exception": sig.exception, "paths": sig.paths, "identifiers": sig.identifiers,
                        "frames": [f.__dict__ for f in sig.frames], "error_signature": sig.error_signature},
            "regions": [r.__dict__ for r in loc.regions],
            "impacts": [{"symbol": i.symbol, "file": i.file, "callers": [f"{c.file}::{c.symbol}" for c in i.callers],
                         "tests": i.tests, "co_changed": i.co_changed, "public_api": i.public_api} for i in loc.impacts],
            "scope_files": loc.scope_files,
            "evidence": [e.id for e in loc.evidence],
        }, indent=2, default=str))
        return

    typer.echo(f"{repo}#{number} @ {sha[:10]}  backend={loc.backend}  confidence={loc.confidence}  difficulty={difficulty(loc)}")
    typer.echo(f"title: {title}")
    typer.echo(f"signals: exception={sig.exception} paths={sig.paths[:5]} identifiers={sig.identifiers[:8]}")
    typer.echo("\nLikely location:")
    for i, r in enumerate(loc.regions, 1):
        typer.echo(f"  {i}. {r.file} L{r.start}-{r.end}  {r.symbol or ''}  score={r.score}  ({'; '.join(r.reasons[:3])})")
    for imp in loc.impacts:
        typer.echo(f"\nImpact of {imp.symbol} ({imp.file}): public_api={imp.public_api}")
        typer.echo("  callers: " + (", ".join(f"{c.file}::{c.symbol}" for c in imp.callers[:8]) or "none found"))
        typer.echo("  tests:   " + (", ".join(imp.tests[:8]) or "none found"))
        typer.echo("  changes with: " + (", ".join(f"{f}({n})" for f, n in imp.co_changed[:5]) or "-"))
    if loc.regions:
        top = loc.regions[0]
        typer.echo(f"\nHistory of {top.file} L{top.start}-{top.end}:")
        for c in region_commits(path, top.file, top.start, top.end, n=5):
            typer.echo(f"  {c.sha[:8]} {c.date[:10]} {c.subject[:70]}{'  [noise]' if c.noise else ''}")
    typer.echo(f"\nScope (files a PR may touch): {', '.join(loc.scope_files[:15])}")


@app.command("sandbox-kit")
def sandbox_kit(dest: str, maintainer: str = typer.Option(..., "--maintainer", "-m", help="your GitHub username")) -> None:
    """Write a ready-to-push sandbox repo (planted bug, policy, verify workflow, sample issue, fix patches)."""
    from pathlib import Path

    from mergemind.sandbox_kit import write_kit

    path = Path(dest)
    if path.exists() and any(path.iterdir()):
        raise typer.BadParameter(f"{dest} exists and is not empty")
    for f in write_kit(path, maintainer):
        typer.echo(f"  {f}")
    typer.echo(f"\nSandbox written to {path.resolve()} (committed on branch main). Next: see KIT_STEPS.md")


@app.command("actions-kit")
def actions_kit(dest: str = typer.Argument(".", help="the repository to deploy MergeMind on")) -> None:
    """Write the always-on GitHub Actions runtime (.github/workflows/mergemind.yml) into a repo."""
    from importlib import resources
    from pathlib import Path

    out = Path(dest) / ".github" / "workflows" / "mergemind.yml"
    if out.exists():
        raise typer.BadParameter(f"{out} already exists")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(resources.files("mergemind.templates").joinpath("mergemind.yml").read_text(encoding="utf-8"),
                   encoding="utf-8")
    typer.echo(f"wrote {out}\n\nThen, in the repository's GitHub settings:\n"
               "  1. Environments -> New environment `mergemind` -> Deployment branches: your default branch only.\n"
               "  2. Add environment secrets MERGEMIND_TOKEN (fine-grained token, see README), NVIDIA_API_KEY and,\n"
               "     optionally, NEATLOGS_API_KEY.\n"
               "  3. Keep .github/workflows/mergemind-verify.yml on the default branch (PR tests, no secrets).\n"
               "  4. Stop any laptop `mergemind run` for this repo: one engine at a time.\n"
               "  5. Commit, push, then run the workflow once from the Actions tab (workflow_dispatch).")


@app.command()
def preflight(repo: str = typer.Option(..., "--repo", "-r")) -> None:
    """Check a repo is ready for MergeMind: token rights, branch protection, policy file."""
    from mergemind.github import access
    from mergemind.github.client import GitHubError

    ctx = _context(need_llm=False)
    ok = True

    def line(passed: bool, text: str) -> None:
        nonlocal ok
        ok &= passed
        typer.echo(f"{'PASS' if passed else 'FAIL'}  {text}")

    try:
        info = ctx.gh.get_repo(repo)
    except GitHubError as e:
        typer.echo(f"FAIL  cannot read {repo}: {e.error_class}")
        raise typer.Exit(1)
    perms = info.get("permissions") or {}
    line(bool(perms.get("push")), "token can write issues, comments and branches (push)")
    line(bool(perms.get("admin")), "token can invite and remove collaborators (admin / Administration: write)")
    branch = info.get("default_branch", "main")
    rules = access.check_branch_rules(ctx.gh, repo, branch)
    line(rules.ok, f"`{branch}` is protected" + ("" if rules.ok else f": missing {'; '.join(rules.missing)}"))
    wf = ctx.gh.get_file(repo, ".github/workflows/mergemind-verify.yml", ref=branch)
    line(wf is not None, "mergemind-verify workflow is on the default branch (runs PR tests without secrets)")
    if wf is not None and "pull_request_target" in wf:
        line(False, "mergemind-verify must use `pull_request`, never `pull_request_target`")
    text = ctx.gh.get_file(repo, ".github/mergemind.yml")
    policy = ctx.policy(repo)
    typer.echo(f"{'PASS' if text else 'INFO'}  policy file {'found' if text else 'missing (defaults apply)'}: "
               f"autonomy={policy.effective_autonomy(ctx.settings)}, access.mode={policy.access.mode}")
    if policy.guard.maintainer_channel:
        try:
            ch = ctx.gh.get_repo(policy.guard.maintainer_channel)
            line(bool(ch.get("private")), f"maintainer channel {policy.guard.maintainer_channel} is private")
        except GitHubError:
            line(False, f"maintainer channel {policy.guard.maintainer_channel} is readable")
    else:
        typer.echo("WARN  no guard.maintainer_channel: held messages and security alerts stay in the local log")
    typer.echo("\nready" if ok else "\nnot ready")
    if not ok:
        raise typer.Exit(1)


eval_app = typer.Typer(no_args_is_help=True, help="Backtests (buildplan-v2 §12).")
app.add_typer(eval_app, name="eval")


@eval_app.command("dataset")
def eval_dataset(repo: list[str] = typer.Option(..., "--repo", "-r"), limit: int = 150) -> None:
    """Fetch closed issues fixed by one merged PR (ground truth) for each repo."""
    from mergemind.evals import dataset

    ctx = _context(need_llm=False)
    for r in repo:
        items, stats = dataset.fetch(ctx.gh, r, ctx.settings.work_dir, limit=limit)
        typer.echo(f"{r}: {len(items)} issues  {json.dumps(stats)}")


@eval_app.command("locate")
def eval_locate(repo: list[str] = typer.Option(..., "--repo", "-r"), run: str = "v1",
                limit: int = 0, methods: str = "entire,lexical,mentioned,recency") -> None:
    """Leakage-free localization backtest (no LLM); resumable."""
    from mergemind.code.repo_cache import RepoCache
    from mergemind.evals import backtest, dataset

    ctx = _context(need_llm=False)
    cache = RepoCache(ctx.settings.work_dir, ctx.settings.github_token)
    for r in repo:
        items = dataset.load(dataset.dataset_path(ctx.settings.work_dir, r))
        if limit:
            items = items[:limit]
        stats = json.loads(dataset.dataset_path(ctx.settings.work_dir, r).with_suffix(".stats.json").read_text())
        cache.ensure(r, fetch=True)
        out = backtest.rows_path(ctx.settings.work_dir, r, run)
        rows = backtest.run(items, cache, stats["default_branch"], out, tuple(methods.split(",")))
        typer.echo(f"{r}: {len(rows)} rows -> {out}")
    tracing.flush()


@eval_app.command("triage")
def eval_triage(repo: list[str] = typer.Option(..., "--repo", "-r"), run: str = "v1", per_repo: int = 8,
                out_dir: str = "evidence/eval", split: str = typer.Option("test", help="test | dev"),
                workers: int = 1) -> None:
    """Full Nemotron triage on a sample of each repo's test split (newest 40%), time-travelled.
    `--split dev` samples the oldest 60% instead, for diagnosing failures without touching the test split."""
    from pathlib import Path

    from mergemind.code.repo_cache import RepoCache
    from mergemind.evals import dataset, triage_eval

    ctx = _context()
    cache = RepoCache(ctx.settings.work_dir, ctx.settings.github_token)
    all_rows, per = [], {}
    for r in repo:
        path = dataset.dataset_path(ctx.settings.work_dir, r)
        items = sorted(dataset.load(path), key=lambda it: it.created_at)
        branch = json.loads(path.with_suffix(".stats.json").read_text())["default_branch"]
        cut = int(len(items) * 0.6)
        pool = items[cut:] if split == "test" else items[:cut]  # newest 40% = test split, never used for tuning
        step = max(1, len(pool) // per_repo)
        sample = pool[::step][:per_repo]
        cache.ensure(r, fetch=False)
        rows = triage_eval.run(ctx.settings, ctx.store, ctx.gh, ctx.llm, cache, sample, branch, run,
                               path.parent / f"triage-{run}.jsonl", workers=workers)
        per[r] = triage_eval.summarize(rows)
        all_rows += rows
        typer.echo(f"{r}: {json.dumps(per[r])}")
    result = {"run": run, "model_investigator": ctx.settings.llm_model_investigator,
              "model_fast": ctx.settings.llm_model_fast, "all": triage_eval.summarize(all_rows), "per_repo": per}
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / f"triage-{run}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    typer.echo(json.dumps(result["all"], indent=2))
    tracing.flush()


@eval_app.command("economics")
def eval_economics(repo: list[str] = typer.Option(..., "--repo", "-r"), run: str = "v1",
                   out_dir: str = "evidence/cfo") -> None:
    """Cost per issue / PR from measured tokens -> cfo.ai drivers (CSV + markdown)."""
    from pathlib import Path

    from mergemind.evals import dataset, economics

    settings = Settings.from_env()
    rows = []
    for r in repo:
        p = dataset.dataset_path(settings.work_dir, r).parent / f"triage-{run}.jsonl"
        if p.exists():
            rows += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    typer.echo(economics.build(rows, settings.db_path, settings.llm_model_investigator, Path(out_dir), run))


@eval_app.command("pr-static")
def eval_pr_static(repo: list[str] = typer.Option(..., "--repo", "-r"), run: str = "v1",
                   out_dir: str = "evidence/eval") -> None:
    """Safety-scan hold rate, reverted PRs and out-of-scope rate on the dataset's merged fix PRs."""
    from pathlib import Path

    from mergemind.code.repo_cache import RepoCache
    from mergemind.config import GuardPolicy
    from mergemind.evals import backtest, dataset, prcheck

    ctx = _context(need_llm=False)
    cache = RepoCache(ctx.settings.work_dir, ctx.settings.github_token)
    all_rows, per_repo, splits = [], {}, {"dev": [], "test": []}
    for r in repo:
        path = dataset.dataset_path(ctx.settings.work_dir, r)
        items = dataset.load(path)
        branch = json.loads(path.with_suffix(".stats.json").read_text())["default_branch"]
        loc_path = backtest.rows_path(ctx.settings.work_dir, r, run)
        scopes = {}
        if loc_path.exists():
            scopes = {row.number: row.scope.get("fused") or row.scope.get("entire") or row.scope.get("lexical") or []
                      for row in backtest.load_rows(loc_path) if not row.error}
        rows = prcheck.run(ctx.gh, items, cache.ensure(r, fetch=False), branch, scopes,
                           GuardPolicy().sensitive_paths)
        prcheck.save(rows, path.parent / f"pr-static-{run}.jsonl")
        dev, test = prcheck.split_rows(rows, items)
        splits["dev"] += dev
        splits["test"] += test
        per_repo[r] = prcheck.summarize(rows)
        all_rows += rows
        typer.echo(f"{r}: {json.dumps(per_repo[r])}")
    result = {"run": run, "all": prcheck.summarize(all_rows), "test": prcheck.summarize(splits["test"]),
              "dev": prcheck.summarize(splits["dev"]), "per_repo": per_repo}
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / f"pr-static-{run}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    typer.echo(json.dumps(result["all"], indent=2))


@eval_app.command("report")
def eval_report(repo: list[str] = typer.Option(..., "--repo", "-r"), run: str = "v1",
                out_dir: str = "evidence/eval") -> None:
    """Metrics tables (all / dev / test split) with bootstrap 95% CIs."""
    from pathlib import Path

    from mergemind.evals import backtest, report

    ctx = _context(need_llm=False)
    rows = []
    for r in repo:
        rows += backtest.load_rows(backtest.rows_path(ctx.settings.work_dir, r, run))
    md, data = report.locate_report(rows, run, repo)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"locate-{run}.md").write_text(md, encoding="utf-8")
    (out / f"locate-{run}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    typer.echo(md)


@eval_app.command("compare")
def eval_compare(repo: list[str] = typer.Option(..., "--repo", "-r"), base: str = "v1", run: str = "v2",
                 out_dir: str = "evidence/eval") -> None:
    """One v1-vs-v2 table over every backtest (test split), paired bootstrap 95% CIs on the difference."""
    import tempfile
    from pathlib import Path

    from mergemind.evals import compare, dataset
    from mergemind.evals.leak_corpus import build_repo, run_corpus
    from mergemind.guard.leak import LeakGuard

    settings = Settings.from_env()
    dirs = [dataset.dataset_path(settings.work_dir, r).parent for r in repo]
    items = []
    for r in repo:
        items += dataset.load(dataset.dataset_path(settings.work_dir, r))
    loc = json.loads((Path(out_dir) / f"locate-{run}.json").read_text(encoding="utf-8"))
    prs = compare.pr_pairs(dirs, items, base, run)
    tri, cost = compare.triage_pairs(dirs, base, run)
    with tempfile.TemporaryDirectory() as d:
        leak_repo = build_repo(Path(d))
        files = [p.relative_to(leak_repo).as_posix() for p in leak_repo.rglob("*") if p.is_file() and ".git" not in p.parts]
        guard = LeakGuard.build(leak_repo, files, [".env*", "**/.env*", "**/secrets/**", "deploy/**", "infra/**"])
        _, leak = run_corpus(guard, use_critic=False)

    n_loc = loc["paired_test"]["Hit@5"]["n"]
    md = [f"# MergeMind {base} vs {run}", "",
          "Every number is on the **test split** (newest 40% of each repo's issues, never used for tuning) of the "
          f"same leakage-free backtest: encode/httpx, pydantic/pydantic, pytest-dev/pytest. Both versions score the "
          "same issues and PRs, so the CI is a paired bootstrap 95% CI of the difference (2,000 resamples). "
          "`*` = the CI excludes 0.", "",
          f"| Measure | n | {base} | {run} | {run} - {base} [95% CI] |", "|---|---|---|---|---|"]
    names = {"Hit@1": "Localization Hit@1 (fix file ranked first)", "Hit@3": "Localization Hit@3",
             "Hit@5": "Localization Hit@5", "MRR": "Localization MRR",
             "Test recall": "Share of the fix PR's test files that MergeMind names"}
    for k, label in names.items():
        p = loc["paired_test"][k]
        md.append(compare.row(label, {"n": p["n"], "v1": p["entire"], "v2": p["fused"], "diff": p["diff"], "ci": p["ci"]}))
    for label, d in prs.items():
        md.append(compare.row(label, d))
    for label, d in tri.items():
        md.append(compare.row(f"Triage (n={cost['n']} sample): {label}", d))
    md += ["", f"Localization rows: n={n_loc} issues. PR rows: merged fix PRs of the test-split issues (all were "
           "accepted by maintainers, so every hold is a false alarm a human must clear). Triage rows: the same 18 "
           "test-split issues as v1, live Nemotron; with n=18 only large changes can show up as significant.", "",
           "## Guardrails that must not regress", "",
           f"- Leak corpus ({leak['leak_cases']} adversarial + {leak['benign_cases']} benign messages): "
           f"**{leak['leaks']} leaks**, {leak['false_redactions']} false redactions (rules only; "
           "`mergemind eval-leak --critic` adds the Nemotron critic).",
           "- Safety-scan unit tests: workflow edits, dependency changes, provider tokens and private keys (also in "
           "test files), known secret values, and risky calls in source are still held "
           "([tests/test_v2_precision.py](../../tests/test_v2_precision.py)).", "",
           "## Triage cost and speed", "",
           f"- Median tokens per issue: {cost['tokens_median_v1']:,.0f} ({base}) vs {cost['tokens_median_v2']:,.0f} ({run}); "
           f"median wall time {cost['seconds_median_v1']:.0f} s vs {cost['seconds_median_v2']:.0f} s "
           f"({run} ran 3 issues in parallel; {cost['cached_calls_v2']} LLM calls served from the cache).",
           f"- Dispositions {base}: {cost['dispositions_v1']}", f"- Dispositions {run}: {cost['dispositions_v2']}", ""]
    if cost["changed"]:
        md += ["| Issue | " + base + " | " + run + " | " + run + " notes |", "|---|---|---|---|"]
        md += [f"| {c['issue']} | {c['v1']} | {c['v2']} | {'; '.join(c['v2_notes'])[:120]} |" for c in cost["changed"]]
    md += ["", "## What changed in v2 and why (each from a v1 measurement)", "",
           "| v1 finding | v2 change | Code |", "|---|---|---|",
           "| 25% of good PRs held; most holds were false positives ([pr-static-v1.json](pr-static-v1.json)) | "
           "risky calls only in source code, the repo's own package and `re.compile` exempt, identifier/URL-aware "
           "entropy rule, code expressions and stand-in passwords are not secrets, tool config in pyproject.toml is "
           "not a dependency change, pure renames are not binaries | [pr/safety.py](../../src/mergemind/pr/safety.py), "
           "[guard/secrets.py](../../src/mergemind/guard/secrets.py) |",
           "| entire-graph and lexical each win on different repos ([locate-v1.md](locate-v1.md)) | weighted RRF of both "
           "rankings (weights tuned on dev) | [code/localize.py](../../src/mergemind/code/localize.py) |",
           "| entire-graph finds fewer of the fix's tests (0.44 vs 0.61) | union of both backends' tests | same |",
           "| 61% of fixes touch a file outside the scope | top-5 ranked files + co-changed files + new files next to "
           "scoped code (size/coverage knee on dev) | [code/scope.py](../../src/mergemind/code/scope.py) |",
           "| triage escalated real bugs after verifier rejections on mechanical slips (dev-split diagnosis, "
           "[triage-dev-v1.log](triage-dev-v1.log)) | decorators are not @mentions; clamp line ranges and fix "
           "region evidence references before the rules run (claims stay strict) | "
           "[agents/verifier.py](../../src/mergemind/agents/verifier.py) |", "",
           f"Details: [locate-{run}.md](locate-{run}.md) · [pr-static-{run}.json](pr-static-{run}.json) · "
           f"[triage-{run}.json](triage-{run}.json) · neatlogs runs tagged `eval:{run}`."]
    out = Path(out_dir)
    (out / f"{base}-vs-{run}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out / f"{base}-vs-{run}.json").write_text(json.dumps({"localization": loc["paired_test"], "pr": prs, "triage": tri,
                                                           "triage_cost": cost, "leak_corpus": leak}, indent=2),
                                               encoding="utf-8")
    typer.echo("\n".join(md))


@eval_app.command("locate-v2")
def eval_locate_v2(repo: list[str] = typer.Option(..., "--repo", "-r"), base: str = "v1", run: str = "v2",
                   out_dir: str = "evidence/eval") -> None:
    """v2 localization (fusion, test union, wider scope) recomputed offline from the `base` rows;
    tuned on the dev split, reported v1 vs v2 on the test split."""
    from pathlib import Path

    from mergemind.code.repo_cache import RepoCache
    from mergemind.evals import backtest, dataset, locate_v2, report

    ctx = _context(need_llm=False)
    cache = RepoCache(ctx.settings.work_dir, ctx.settings.github_token)
    rows, items, paths = [], {}, {}
    for r in repo:
        rows += backtest.load_rows(backtest.rows_path(ctx.settings.work_dir, r, base))
        items.update({(it.repo, it.number): it for it in dataset.load(dataset.dataset_path(ctx.settings.work_dir, r))})
        paths[r] = cache.path(r)
    v2_rows, tuning = locate_v2.build(rows, items, paths)
    for r in repo:
        locate_v2.save([x for x in v2_rows if x.repo == r], backtest.rows_path(ctx.settings.work_dir, r, run))
    md, data = report.locate_report(v2_rows, run, repo)
    usable = [x for x in v2_rows if not x.error and x.truth]
    dev, test = report.split(usable)
    cmp_test, cmp_dev = report.paired(test, "entire", "fused"), report.paired(dev, "entire", "fused")
    scope = locate_v2.scope_summary(v2_rows, items, paths)
    f, s = tuning["fusion"], tuning["scope"]
    head = [f"# Localization `{run}` vs `{base}` (offline, from the {base} backtest rows)", "",
            "v2 = weighted RRF of the entire-graph and lexical rankings + test union + widened scope. Nothing was "
            f"re-run: the {base} rows hold each backend's ranking, tests and scope at the leakage-free snapshot, and "
            "co-change history is read at the same snapshot commit.", "",
            f"Tuned on the dev split only (n={len(dev)}): fusion k={f['k']}, entire weight {f['w_entire']} "
            f"(dev MRR {f['MRR']:.3f}); scope base={s['base']}, top_ranked={s['top_ranked']}, "
            f"co-changed: top {s['co_change_top']} files (seen together >= {s['co_change_min']}x) around the top "
            f"{s['co_change_files']} ranked files. Scope rule (dev only): widen while each extra source file in the "
            f"median scope adds >= {locate_v2.MIN_GAIN_PER_FILE * 100:.0f} points of all-in-scope rate. The first rule "
            f"tried (median size <= {locate_v2.SCOPE_BUDGET}x v1's = {s['budget']:.0f} files) could not widen at all "
            "because v1's median is 2 files; it was replaced before looking at the test split. Dev frontier: "
            + ", ".join(f"{x['median_source_files']:.0f} files -> {x['all_in_scope']:.2f}" for x in s["frontier"][:8])
            + ".", "",
            report.paired_table(cmp_test, "entire", "fused", f"Test split, v1 vs v2 (n={len(test)}) — headline"), "",
            "### Scope: fix PRs whose source changes all fall inside the allowed scope", "",
            "| Split | v1 all-in-scope | v2 all-in-scope | v1 median size | v2 median size |", "|---|---|---|---|---|"]
    for name in ("test", "dev"):
        a, b = scope[name]["v1"], scope[name]["v2"]
        head.append(f"| {name} (n={a['n']}) | {report._cell(a['all_in_scope'])} | {report._cell(b['all_in_scope'])} | "
                    f"{a['median_source_files']:.0f} | {b['median_source_files']:.0f} |")
    head += ["", "Size = source files in scope (tests and docs are always allowed). v2 also allows new files created "
             "next to scoped source files.", "",
             report.paired_table(cmp_dev, "entire", "fused", f"Dev split (n={len(dev)}, used for tuning)"), "", "---", ""]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"locate-{run}.md").write_text("\n".join(head) + md, encoding="utf-8")
    data.update({"base": base, "paired_test": cmp_test, "paired_dev": cmp_dev, "scope": scope,
                 "tuning": {k: v for k, v in tuning.items() if not k.endswith("_grid")}})
    (out / f"locate-{run}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (out / f"locate-{run}-tuning.json").write_text(json.dumps(tuning, indent=2), encoding="utf-8")
    typer.echo("\n".join(head))


@app.command()
def prompts(push: bool = typer.Option(False, "--push", help="save local prompts as new neatlogs versions"),
            label: str = "production") -> None:
    """Show the prompt versions in use; --push uploads the local prompts to neatlogs Prompts."""
    from mergemind import prompts as P

    ctx = _context(need_llm=False)
    if push:
        if not tracing.enabled():
            raise typer.BadParameter("NEATLOGS_API_KEY is not set")
        for line in P.push(label):
            typer.echo(line)
        P._cache.clear()
    for name, text in P.registry().items():
        _, version = P.get(name, text, label)
        typer.echo(f"{P.PREFIX}{name:<18} {version}")
    tracing.flush()


@app.command("eval-leak")
def eval_leak(critic: bool = typer.Option(False, "--critic", help="also run the Nemotron leak critic")) -> None:
    """Run the seeded leak corpus through the guard and print leak / false-redaction rates."""
    import tempfile
    from pathlib import Path

    from mergemind.evals.leak_corpus import build_repo, run_corpus
    from mergemind.guard.leak import LeakGuard

    ctx = _context(need_llm=critic)
    with tempfile.TemporaryDirectory() as d:
        repo = build_repo(Path(d))
        files = [p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts]
        guard = LeakGuard.build(repo, files, [".env*", "**/.env*", "**/secrets/**", "deploy/**", "infra/**"],
                                llm=ctx.llm, store=ctx.store)
        outcomes, metrics = run_corpus(guard, use_critic=critic)
    for o in outcomes:
        flag = "ok  " if o.ok else "FAIL"
        typer.echo(f"{flag} {o.case.name:24} {o.decision:20} leaked={o.leaked or '-'}")
    typer.echo(json.dumps(metrics, indent=2))
    tracing.flush()
    if metrics["leaks"]:
        raise typer.Exit(1)


@app.command()
def tick(repo: list[str] = typer.Option(..., "--repo", "-r"), dry_run: bool = False) -> None:
    """Run one pass: ingest, timers, events. Used by the GitHub Actions runtime."""
    from mergemind.engine.loop import tick as do_tick

    typer.echo(json.dumps(do_tick(_context(dry_run=dry_run), repo)))


@app.command()
def run(repo: list[str] = typer.Option(..., "--repo", "-r"), dry_run: bool = False) -> None:
    """Run continuously (laptop mode)."""
    from mergemind.engine.loop import run_forever

    run_forever(_context(dry_run=dry_run), repo)


def main() -> None:
    import sys

    # model output can contain any Unicode; never crash on a legacy Windows code page
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    app()
