# MergeMind

**A GitHub agent that takes an issue from "opened" to "merged fix", with the person who reported it as the
contributor, and without leaking anything that person shouldn't see.**

## The problem

Open-source and inner-source maintainers sit between two bad options. They can fix every reported bug
themselves, or they can hand the reporter access, explain the code base, review a PR from a stranger, and hope
nothing sensitive leaks and nothing breaks. The reporter often understands the bug best, but onboarding them
costs more than the fix.

## What MergeMind does

1. **Triage.** It reads a new issue, investigates the actual code with **NVIDIA Nemotron** (Super with reasoning
   for the investigation, Lightning for fast checks) and **entire-graph**, and decides: accept, needs info,
   duplicate, question, feature review, security, or escalate. Every claim it makes cites evidence (code
   regions, call-graph impact, earlier issues). A rule-based verifier and a separate critic must pass before
   anything is posted.
2. **Access.** On an accepted bug, the reporter types `/contribute` and gets time-limited push access: a
   collaborator invite, or fork mode if that's safer. They also get a briefing: where the bug is, which files are in
   scope, the acceptance criteria, and a regression-test hint. Access is checked halfway through its time limit and
   revoked when it runs out.
3. **PR analysis.** It checks the PR in this order:
   - merge conflicts (`git merge-tree`);
   - scope and impact on callers and tests;
   - the test results, which come from a sandboxed workflow with no secrets: a new regression test must fail
     on the base and pass on the head;
   - a rules-only safety scan (CI edits, dependency changes, risky calls, committed secrets);
   - a Nemotron check of each acceptance criterion.
4. **Leak guard.** Everything MergeMind posts passes through it first: mention escaping, a secret scanner (it also
   learns values from the repo's restricted files), restricted and out-of-scope paths, and a Nemotron critic that
   fails closed.
5. **Merge gate.** Only a maintainer's `/mergemind approve` from someone other than the author leads to a
   merge, and only after these steps:
   - the approved commit is re-verified against the latest base;
   - GitHub's update-branch merge is checked to be the only thing added since approval;
   - the merge is pinned to that exact SHA.

   After merging, MergeMind watches the main branch's CI for a few minutes and opens a revert PR if it breaks.

## How it's built

| Piece | Role |
|---|---|
| NVIDIA Nemotron 3 Super 120B (reasoning on) | investigator agent: read-only tools, evidence ledger, 1 repair round |
| NVIDIA Nemotron 3.5 Lightning 30B | duplicate adjudication, verifier critic, PR criteria check, leak critic |
| Entire: entire-graph | code search, definitions, impact (callers, tests, co-change); fused with a git-grep backend in v2 |
| Entire CLI | `entire enable` checkpoints and `entire graph init-agents` while building MergeMind |
| neatlogs | a trace for every issue and PR (one session per issue), prompts versioned in neatlogs Prompts, runs tagged `eval:v1` / `eval:v2` / `live` |
| cfo.ai | unit economics from measured tokens: drivers and an entry sheet for the business model |
| Python 3.12, SQLite, httpx, Typer | pure state machine (`transition(state, event) -> (state, effects)`), idempotent effects, durable timers |
| GitHub Actions | `mergemind-verify.yml` runs untrusted PR tests without secrets; `mergemind.yml` is the always-on runtime |

Design choices that matter:
- **The LLM never writes to GitHub.** It only has read-only tools; every write goes through a rule-based policy
  gate.
- **Issue and PR text are data, never instructions.**
- **Restricted files** (`.env`, `deploy/`, keys) are never shown to the model.
- **Kill switches:** `/mergemind stop` and `MERGEMIND_AUTONOMY=observe`.

## Results

Leakage-free backtest on **450 real issues** and their **438 merged fix PRs** (encode/httpx,
pydantic/pydantic, pytest-dev/pytest). Each issue is replayed on the code as it was before the issue's month.
v2 was tuned on the oldest 60% only. The table below is the newest 40%, with paired bootstrap 95% CIs of the
difference.

| Measure (test split) | v1 | v2 | v2 − v1 [95% CI] |
|---|---|---|---|
| Fix file in the top 3 | 0.68 | 0.74 | +0.07 [+0.01, +0.13] |
| Fix file in the top 5 (best baseline 0.44, issue-mentioned files 0.15) | 0.77 | 0.81 | +0.05 [−0.01, +0.10] |
| Localization MRR | 0.58 | 0.62 | +0.05 [+0.01, +0.08] |
| Fix PR's test files that MergeMind names | 0.50 | 0.74 | +0.24 [+0.18, +0.31] |
| Good merged PRs held by the safety scan (false alarms) | 12% | 5% | −0.07 [−0.11, −0.04] |
| Fix PRs touching a source file outside the allowed scope | 58% | 47% | −0.11 [−0.16, −0.07] |
| Full Nemotron triage (18 issues): real bugs accepted | 9/18 | 9/18 | 0.00 [−0.28, +0.28] |
| Full Nemotron triage: investigator's fix file ranked first | 0.50 | 0.56 | +0.06 [0.00, +0.17] |
| Leak corpus: leaks | 0 | 0 | — |

Full table, including the PR-side rows and what each v2 change fixed: [evidence/eval/v1-vs-v2.md](evidence/eval/v1-vs-v2.md).
Failure recovery under injected GitHub 500/429/403, Nemotron 429, malformed tool calls and graph timeouts:
[evidence/recovery.md](evidence/recovery.md).

**The failure-to-fix loop (neatlogs + backtests):** each v2 change starts from a v1 measurement.
- A 25% false-alarm rate on good PRs led to the safety-scan precision rules.
- entire-graph and lexical search each winning on different repos led to fused rankings.
- 61% of fixes landing out of scope led to the co-change scope.
- Verifier rejections on mechanical slips, found on the dev split, led to the verifier fix-ups.

## Cost and business model (cfo.ai)

Triage costs about **$0.007 per issue** (v2, median 55k tokens) at hosted Nemotron list prices, and **$0** on the
free NVIDIA endpoint with the self-hosted GitHub Action.

**Live cfo.ai model: https://cfo.ai/s/PG56dy5YWbPcGD_b** It runs 24 months with Base, Growth and Downside
scenarios, and its pages are Assumptions, Growth & accounts, and P&L & cash. The AI cost per issue is measured by
our backtest. Prices, growth, conversion and churn are estimates. Team cost and starting cash are demo
placeholders.

| Scenario | Stops burning cash | Month-24 revenue | Gross margin |
|---|---|---|---|
| Base (200 new free repos/month, 2% convert, 3% churn) | month 4 | $32.2k/month | 91% |
| Growth (600, 3%, 2%) | month 2 | $167.3k/month | 91% |
| Downside (80, 1%, 5%, AI prices ×2) | month 7 | $4.9k/month | 85% |

- **Unit margins:** a $19 Pro repo costs us about $1.42 a month (93% margin), and a $149 Team account (10 repos)
  costs about $14.21 (90%).
- **Price risk:** doubling AI prices costs about 6 margin points. Growth and conversion matter far more than the
  model price.
- **Break-even depends on team cost:** with a real team cost (for example one engineer at about $15k a month),
  Base breaks even around month 15.

Drivers, formulas and the step-by-step entry sheet: [evidence/cfo/](evidence/cfo/cfo_entry_sheet.md).

## Try it

```bash
uv sync
uv run mergemind locate https://github.com/pallets/click/issues/3362        # no LLM: where is the bug?
uv run mergemind triage https://github.com/pallets/click/issues/3362        # full triage, never writes
uv run mergemind sandbox-kit ../mergemind-sandbox -m <your-github-user>     # end-to-end sandbox
uv run mergemind actions-kit .                                              # always-on via GitHub Actions
uv run pytest -q                                                            # 171 tests, scenarios S1–S16
```

The keys go in `.env.local` (`NVIDIA_API_KEY`, `GITHUB_TOKEN`, optional `NEATLOGS_API_KEY`). For the end-to-end
run, `sandbox-kit` writes a `KIT_STEPS.md` guide into the sandbox it creates; the full design is in
[buildplan-v2.md](buildplan-v2.md).

## Evidence for judges

| Checklist item | Where to check it |
|---|---|
| The agent improved (before and after runs, with numbers) | [evidence/eval/v1-vs-v2.md](evidence/eval/v1-vs-v2.md): runs tagged `eval:v1` and `eval:v2` in neatlogs |
| You used neatlogs | [evidence/neatlogs/](evidence/neatlogs/README.md): dashboard, sessions per issue, a full triage trace |
| You used Entire | [evidence/entire/](evidence/entire/README.md): entire-graph ranked a live bug's function #1, impact output, 12 checkpoints |
| You used cfo.ai | [live model](https://cfo.ai/s/PG56dy5YWbPcGD_b), [screenshots](evidence/cfo/screenshots/), [entry sheet](evidence/cfo/cfo_entry_sheet.md) |
| The agent recovers from failures | [evidence/recovery.md](evidence/recovery.md): 4 faults injected in one live run |
| You built in public | [#neatHack post on X](https://x.com/AkshayRsnrp/status/2109294188894490624) |
| You wrote the code during the hackathon | [commit history](https://github.com/Im-Akii/MergeMind/commits/main): first commit Oct 10, 2026 |
| It runs live | [akii-codes/mergemind-tests-2.0](https://github.com/akii-codes/mergemind-tests-2.0): [issue #1](https://github.com/akii-codes/mergemind-tests-2.0/issues/1) → [PR #2](https://github.com/akii-codes/mergemind-tests-2.0/pull/2), screenshots in [evidence/live/](evidence/live/) |

## Honest limits

- **Small triage sample.** The full-LLM triage backtest is 18 issues, so only large changes can be shown as
  significant. Localization and PR rows use the full 166–180 test-split items.
- **Triage acceptance did not improve.** It is 9/18 real bugs in both v1 and v2. Three earlier misses are now
  accepted (two "needs info" and one false duplicate), but two v2 reports still failed the verifier after a
  repair. The remaining misses ask a human: feature-like reports always go to maintainers. That costs a
  maintainer a look, but it is never an unsafe action. Next step: a second repair round when only one claim
  fails.
- **Remaining safety-scan holds** on good PRs are dependency bumps, build-script and CI changes. A maintainer
  should look at those.
- **Narrow history check.** Only 2 of the 438 fix PRs were later reverted, too few to measure how often the scan
  catches bad PRs.
