"""Issue triage pipeline (buildplan-v2 §7.3):
fast gates → repo brief → localization → duplicates → investigator → verifier (+1 repair)
→ rule-based disposition → scope."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mergemind.agents.investigator import Budget, Investigator, InvestigatorReport
from mergemind.agents.ledger import Ledger
from mergemind.agents.tools import ToolBox
from mergemind.agents.verifier import Verdict, to_json, verify
from mergemind.code.graph import code_intels, is_doc, is_test
from mergemind.code.localize import Localization, difficulty, localize_fused
from mergemind.code.profile import get_brief
from mergemind.code.repo_cache import RepoCache, git
from mergemind.code.signals import extract
from mergemind.config import Policy, Settings
from mergemind.github.client import GitHub
from mergemind.guard.paths import PathGuard
from mergemind.llm import LLM, Usage
from mergemind.models import MissingField, Region, TriageReport
from mergemind.retrieval.docs import DocsIndex
from mergemind.retrieval.dupes import DupCandidate, IssueIndex, adjudicate
from mergemind.store.db import Store
from mergemind.text import clean, issue_for_model
from mergemind.tracing import span

log = logging.getLogger(__name__)

AGENT_VERSION = "m2"

SECURITY_TERMS = re.compile(
    r"\b(CVE-\d{4}-\d+|RCE|remote code execution|XSS|cross[- ]site scripting|SQL injection|SQLi|"
    r"auth(?:entication)? bypass|privilege escalation|path traversal|SSRF|CSRF|deserialization|"
    r"leaked (?:token|secret|key|password)|exposed (?:token|secret|credentials?)|exploit|vulnerab\w*|security issue)\b",
    re.I,
)
_LINK = re.compile(r"https?://\S+")


@dataclass
class TriageOutcome:
    report: TriageReport
    run_id: int | None
    difficulty: str = "medium"
    gate: str | None = None
    verdict: Verdict | None = None
    usage: Usage = field(default_factory=Usage)
    tool_calls: int = 0
    repairs: int = 0
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------- fast gates


def fast_gate(issue: dict[str, Any]) -> tuple[str | None, bool]:
    """Returns (immediate disposition or None, possible_security)."""
    body = clean(issue.get("body"))
    meaningful = re.sub(r"[\W_]+", "", _LINK.sub("", body))
    links = _LINK.findall(body)
    if links and len(meaningful) < 40 and len(links) >= 2:
        return "spam", False
    security = bool(SECURITY_TERMS.search(f"{issue.get('title', '')}\n{body}"))
    if len(meaningful) < 20 and not security:
        return "needs_info", False
    return None, security


def _gate_report(kind: str, issue: dict[str, Any]) -> TriageReport:
    if kind == "spam":
        return TriageReport(issue_type="spam", summary="Only links or promotional content.",
                            disposition="escalate", disposition_confidence=0.6)
    return TriageReport(
        issue_type="bug", summary="The issue has too little detail to investigate.", disposition="needs_info",
        missing_info=[MissingField(field="description", why="what happened, what you expected, and steps to reproduce"),
                      MissingField(field="environment", why="versions and platform")],
        disposition_confidence=0.9)


# --------------------------------------------------------------- disposition


def decide(report: InvestigatorReport, loc: Localization, verdict: Verdict, ledger: Ledger,
           possible_security: bool) -> tuple[str, list[str]]:
    """Rule-based disposition after verification (buildplan §7.7). Returns (disposition, notes)."""
    notes: list[str] = []
    d = report.disposition
    if d == "security":
        return "security", notes
    if not verdict.ok:
        return "escalate", ["verifier rejected the report twice"]
    if possible_security and d != "security":
        notes.append("security keywords present but the investigator cleared them")
    if report.disposition_confidence < 0.4:
        return "escalate", [*notes, f"low disposition confidence {report.disposition_confidence:.2f}"]
    strong_loc = bool(report.localization) and (report.localization_confidence >= 0.6 or loc.confidence == "high")
    if d == "duplicate":
        return "duplicate", notes
    # feature requests are a maintainer's call, whatever the investigator chose
    if report.issue_type == "feature" and d in ("accept", "needs_info"):
        return "feature_review", [*notes, f"feature request (investigator chose {d})"]
    if d == "needs_info":
        # don't hold up a clearly localized bug over paperwork; only when the root cause is pinned down
        pinned = (report.issue_type == "bug" and report.localization_confidence >= 0.75
                  and any(r.role == "root_cause" for r in report.localization))
        if pinned and report.acceptance_criteria:
            notes.append("info missing but localization is strong: accepted, with the gaps as open questions")
            return "accept", notes
        return "needs_info", notes
    if d == "question":
        cites_docs = any(ledger.get(e) and ledger.get(e).kind == "doc" for c in report.claims for e in c.evidence)
        return ("question", notes) if cites_docs else ("escalate", [*notes, "question without doc evidence"])
    if d == "feature_review":
        return "feature_review", notes
    if d == "out_of_scope":
        cites_docs = any(ledger.get(e) and ledger.get(e).kind == "doc" for c in report.claims for e in c.evidence)
        ok = cites_docs and report.disposition_confidence >= 0.7
        return ("out_of_scope", notes) if ok else ("escalate", [*notes, "out of scope without strong doc evidence"])
    if d == "accept":
        if not strong_loc and report.localization_confidence < 0.4:
            return "escalate", [*notes, "accepted with weak localization"]
        return "accept", notes
    return "escalate", notes


def compute_scope(report: InvestigatorReport, loc: Localization, intel: Any, guard: PathGuard,
                  repo: Path | None = None) -> list[Region]:
    """Scope = localized regions + callers and tests of their symbols, widened (v2) with the top
    ranked files and their co-changed files (computed by code, not the LLM; code/scope.py)."""
    scope: dict[tuple, Region] = {}
    for r in report.localization:
        scope.setdefault((r.file, r.start), r)
    seen_symbols = {(i.file, i.symbol) for i in loc.impacts}
    impacts = list(loc.impacts)
    for r in report.localization[:3]:
        if r.symbol and (r.file, r.symbol) not in seen_symbols:
            impacts.append(intel.impact(r.symbol, r.file))
    loc_files = {r.file for r in report.localization}
    for imp in impacts:
        if imp.file and imp.file not in loc_files:
            continue  # only impacts of regions the report kept
        for c in imp.callers[:10]:
            if not is_doc(c.file):
                scope.setdefault((c.file, c.start), Region(evidence="impact", file=c.file, start=c.start,
                                                           end=c.end, symbol=c.symbol, role="related"))
        for t in imp.tests[:10]:
            scope.setdefault((t, 1), Region(evidence="impact", file=t, start=1, end=1, symbol=None, role="related"))
    if repo is not None:
        from mergemind.code.scope import widen

        have = {r.file for r in scope.values()}
        ranked = list(dict.fromkeys([r.file for r in report.localization] + [r.file for r in loc.regions]))
        for f in widen([], ranked, repo):
            if f not in have and not is_test(f):
                have.add(f)
                scope[(f, 1)] = Region(evidence="scope", file=f, start=1, end=1, symbol=None, role="related")
    return [r for r in scope.values() if not guard.is_sensitive(r.file)]


# ------------------------------------------------------------------- pipeline


def _context(sig, loc: Localization, dups: list[DupCandidate], ledger: Ledger, possible_security: bool,
             issue_ev_id: str) -> str:
    lines = [f"[{issue_ev_id}] this issue's own text (cite it for what the reporter says or observed)"]
    if possible_security:
        lines.append("NOTE: a keyword gate flagged possible security content. Confirm (disposition security) or clear it.")
    lines.append(f"Signals: exception={sig.exception} error_signature={sig.error_signature} "
                 f"paths={sig.paths[:5]} identifiers={sig.identifiers[:10]} versions={sig.versions[:3]}")
    lines.append(f"\nCode localization candidates (confidence {loc.confidence}, backend {loc.backend}):")
    for ev, r in zip([e for e in loc.evidence if e.kind == "code_region"], loc.regions):
        lines.append(f"[{ev.id}] {r.file} L{r.start}-{r.end} {r.symbol or ''} ({'; '.join(r.reasons[:2])})")
    for ev in (e for e in loc.evidence if e.kind == "impact"):
        lines.append(f"[{ev.id}] impact {ev.locator}: {ev.excerpt}")
    if dups:
        lines.append("\nEarlier similar issues (adjudicated by a separate model):")
        for c in dups[:5]:
            a = c.adjudication
            ev = ledger.add("issue", f"#{c.issue.number}", f"{c.issue.title}\n{c.issue.body[:800]}",
                            meta={"state": c.issue.state, "relation": a.relation if a else None,
                                  "confidence": a.confidence if a else None, "verified_duplicate": c.verified})
            adj = (f" relation={a.relation} confidence={a.confidence:.2f} shared={a.shared_facts[:3]}"
                   f" verified_duplicate={c.verified}") if a else ""
            lines.append(f"[{ev.id}] #{c.issue.number} [{c.issue.state}] {c.issue.title}{adj}")
    return "\n".join(lines)


@span("WORKFLOW", "triage")
def triage_issue(
    *,
    settings: Settings,
    store: Store,
    gh: GitHub,
    llm: LLM,
    policy: Policy,
    repo: str,
    number: int,
    cache: RepoCache | None = None,
    as_of_sha: str | None = None,
    eval_run_id: str | None = None,
    budget: Budget | None = None,
    fetch: bool = True,
) -> TriageOutcome:
    issue = gh.get_issue(repo, number)
    comments = gh.list_issue_comments(repo, number) if issue.get("comments") else []
    comments = [c for c in comments if "<!-- mergemind:" not in (c.get("body") or "")]
    issue_text = issue_for_model(issue, comments)

    gate, possible_security = fast_gate(issue)
    if gate:
        report = _gate_report(gate, issue)
        run_id = store.start_run(repo, number, "triage", agent_version=AGENT_VERSION, prompt_version="gate",
                                 eval_run_id=eval_run_id)
        store.finish_run(run_id, "ok", report_json=report.model_dump_json())
        return TriageOutcome(report, run_id, gate=gate)

    cache = cache or RepoCache(settings.work_dir, settings.github_token)
    private = bool(gh.get_repo(repo).get("private"))
    path = cache.ensure(repo, fetch=fetch, private=private)
    sha = as_of_sha or cache.head(repo)
    cache.checkout_detached(repo, sha)
    files = [f for f in git(path, "ls-files").splitlines() if f]
    fileset = set(files)
    guard = PathGuard(policy.guard.sensitive_paths)
    brief, _meta = get_brief(store, repo, path, guard.filter(files), sha)

    run_id = store.start_run(repo, number, "triage", agent_version=AGENT_VERSION, as_of_sha=sha,
                             prompt_version="investigator-v1", model=settings.llm_model_investigator,
                             eval_run_id=eval_run_id)
    ledger = Ledger(store, run_id, sha)
    issue_ev = ledger.add("issue", f"#{number}", clean(f"{issue.get('title')}\n{issue.get('body')}"), meta={"self": True})
    usage = Usage()

    # plain-code localization first: grounds the agent and costs no tokens
    title, body = clean(issue.get("title"), 300), clean(issue.get("body"))
    sig = extract(f"{title}\n{body}", fileset)
    intels = code_intels(path, faults=llm.faults)
    intel = next(iter(intels.values()))  # entire-graph when installed: the investigator's tools and impact
    loc = localize_fused(intels, path, title, body, sig, sha=sha)
    loc.regions = [r for r in loc.regions if not guard.is_sensitive(r.file)]
    for ev in loc.evidence:
        if not guard.is_sensitive(ev.locator.split("#")[0].split("::")[0]):
            ledger.adopt(ev)

    # duplicates: retrieval over earlier issues, adjudicated pairwise
    index = IssueIndex(store, repo)
    index.refresh(gh, fileset)
    dups = index.candidates(number, title, body, issue["created_at"], sig.error_signature)
    usage.add(adjudicate(llm, issue_text, dups))

    tools = ToolBox(repo=path, files=guard.filter(files), intel=intel, ledger=ledger, guard=guard,
                    docs=DocsIndex(path, files, guard), issues=index, issue_number=number,
                    issue_created_at=issue["created_at"])
    inv = Investigator(llm, tools, budget)
    result = inv.run(brief, issue_text, _context(sig, loc, dups, ledger, possible_security, issue_ev.id))
    verdict: Verdict | None = None
    notes: list[str] = []

    if result.report is not None:
        verdict = verify(llm, result.report, ledger, path, fileset, guard, dups, issue["created_at"], issue_text)
        if not verdict.ok:
            log.info("verifier rejected report: %s", verdict.failures)
            usage.add(verdict.usage)
            result = inv.repair(verdict.feedback())
            if result.report is not None:
                verdict = verify(llm, result.report, ledger, path, fileset, guard, dups, issue["created_at"], issue_text)
        usage.add(verdict.usage)

    usage.add(result.usage)
    if result.report is None:
        final = TriageReport(issue_type="bug", summary="MergeMind could not complete the investigation.",
                             disposition="escalate", disposition_confidence=0.0)
        notes.append(f"no report ({result.error or 'budget exhausted'})")
    else:
        r = result.report
        disposition, notes = decide(r, loc, verdict, ledger, possible_security)
        scope = compute_scope(r, loc, intel, guard, path) if disposition == "accept" else []
        open_q = list(r.open_questions)
        if disposition == "accept" and r.missing_info:
            open_q += [f"{m.field}: {m.why}" for m in r.missing_info]
        final = TriageReport(
            issue_type=r.issue_type, summary=r.summary, disposition=disposition,
            duplicate_of=r.duplicate_of if disposition == "duplicate" else None,
            missing_info=r.missing_info if disposition == "needs_info" else [],
            localization=r.localization, localization_confidence=r.localization_confidence,
            scope=scope, acceptance_criteria=r.acceptance_criteria,
            regression_test_hint=r.regression_test_hint, open_questions=open_q,
            claims=r.claims, disposition_confidence=r.disposition_confidence,
        )

    from mergemind import tracing

    tracing.attrs(repo=repo, issue=number, disposition=final.disposition, issue_type=final.issue_type,
                  localized_files=[r.file for r in final.localization], scope_size=len(final.scope),
                  tool_calls=result.tool_calls, tool_call_repairs=result.repairs,
                  verifier_ok=None if verdict is None else verdict.ok, notes=notes, backend=loc.backend,
                  in_tokens=usage.in_tok, out_tokens=usage.out_tok, cached_calls=usage.cached,
                  prompt_version=inv.prompt_version, eval_run_id=eval_run_id, as_of_sha=sha)
    store.add_tokens(run_id, usage.in_tok, usage.out_tok)
    store.finish_run(run_id, "ok" if result.report else "escalated", report_json=final.model_dump_json(),
                     verdict_json=None if verdict is None else to_json(verdict))
    return TriageOutcome(final, run_id, difficulty(loc), None, verdict, usage, result.tool_calls,
                         result.repairs, notes)
