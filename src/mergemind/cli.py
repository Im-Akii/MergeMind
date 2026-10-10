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
    from mergemind.code.graph import code_intel
    from mergemind.code.history import region_commits
    from mergemind.code.localize import difficulty, localize
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
    intel = code_intel(path)
    loc = localize(intel, path, title, body, sig, sha=sha)
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
                out_dir: str = "evidence/eval") -> None:
    """Full Nemotron triage on a sample of each repo's test split (newest 40%), time-travelled."""
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
        test = items[int(len(items) * 0.6):]  # newest 40% = test split, never used for tuning
        step = max(1, len(test) // per_repo)
        sample = test[::step][:per_repo]
        cache.ensure(r, fetch=False)
        rows = triage_eval.run(ctx.settings, ctx.store, ctx.gh, ctx.llm, cache, sample, branch, run,
                               path.parent / f"triage-{run}.jsonl")
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
    typer.echo(economics.build(rows, settings.db_path, settings.llm_model_investigator, Path(out_dir)))


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
    all_rows, per_repo = [], {}
    for r in repo:
        path = dataset.dataset_path(ctx.settings.work_dir, r)
        items = dataset.load(path)
        branch = json.loads(path.with_suffix(".stats.json").read_text())["default_branch"]
        loc_path = backtest.rows_path(ctx.settings.work_dir, r, run)
        scopes = {}
        if loc_path.exists():
            scopes = {row.number: row.scope.get("entire") or row.scope.get("lexical") or []
                      for row in backtest.load_rows(loc_path) if not row.error}
        rows = prcheck.run(ctx.gh, items, cache.ensure(r, fetch=False), branch, scopes,
                           GuardPolicy().sensitive_paths)
        prcheck.save(rows, path.parent / f"pr-static-{run}.jsonl")
        per_repo[r] = prcheck.summarize(rows)
        all_rows += rows
        typer.echo(f"{r}: {json.dumps(per_repo[r])}")
    result = {"run": run, "all": prcheck.summarize(all_rows), "per_repo": per_repo}
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
