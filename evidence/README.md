# Evidence index

All backtests are leakage-free: each issue is replayed on the code as it was before the issue's
month, and scored against the merged PR that fixed it. Headline numbers are on the test split
(newest 40% per repo), which was never used for tuning.

| What | Where | Tool used |
|---|---|---|
| **v1 vs v2, every measure, paired 95% CIs (start here)** | [eval/v1-vs-v2.md](eval/v1-vs-v2.md), [eval/v1-vs-v2.json](eval/v1-vs-v2.json) | neatlogs (`eval:v1`, `eval:v2`) |
| Backtest v1 summary and the v2 plan it produced | [eval/summary-v1.md](eval/summary-v1.md) | — |
| Localization v1: 450 issues, baselines + entire-graph ablation | [eval/locate-v1.md](eval/locate-v1.md), [eval/locate-v1.json](eval/locate-v1.json), log [eval/locate-v1.log](eval/locate-v1.log) | Entire (entire-graph) |
| Localization v2: fused backends, test union, wider scope (dev-tuned) | [eval/locate-v2.md](eval/locate-v2.md), [eval/locate-v2.json](eval/locate-v2.json), tuning grid [eval/locate-v2-tuning.json](eval/locate-v2-tuning.json) | Entire + lexical |
| PR safety scan / scope on 438 merged fix PRs, v1 and v2 | [eval/pr-static-v1.json](eval/pr-static-v1.json), [eval/pr-static-v2.json](eval/pr-static-v2.json) | — |
| Full Nemotron triage, 18 test-split issues, v1 and v2 | [eval/triage-v1.json](eval/triage-v1.json) ([log](eval/triage-v1.log)), [eval/triage-v2.json](eval/triage-v2.json) ([log](eval/triage-v2.log)) | NVIDIA Nemotron, neatlogs |
| Triage failure diagnosis on the dev split (drove the verifier fixes) | [eval/triage-dev-v1.json](eval/triage-dev-v1.json), log [eval/triage-dev-v1.log](eval/triage-dev-v1.log) | NVIDIA Nemotron, neatlogs (`eval:dev-v1`) |
| Failure recovery (live fault injection + scenario tests) | [recovery.md](recovery.md), log [eval/faults-v1.log](eval/faults-v1.log) | neatlogs (`eval:faults`) |
| Leak guard corpus | `uv run mergemind eval-leak --critic` (0 leaks, 0 false redactions); also in [eval/v1-vs-v2.md](eval/v1-vs-v2.md) | Nemotron Lightning |
| Unit economics → cfo.ai (v2 measurements) | [cfo/unit_economics.md](cfo/unit_economics.md), [cfo/cfo_drivers.csv](cfo/cfo_drivers.csv), entry sheet [cfo/cfo_entry_sheet.md](cfo/cfo_entry_sheet.md), **live model https://cfo.ai/s/PG56dy5YWbPcGD_b** | cfo.ai |
| **neatlogs screenshots**: dashboard, sessions per issue, a full triage trace with `[REDACTED]` keys | [neatlogs/](neatlogs/README.md) | neatlogs |
| **entire-graph output**: it ranked the function behind a live bug #1; impact analysis; Entire checkpoints | [entire/](entire/README.md) | Entire |
| **cfo.ai model screenshots**: assumptions, growth & accounts, P&L & cash | [cfo/screenshots/](cfo/screenshots/) | cfo.ai |
| **Live run on GitHub Actions**: issue → triage → `/contribute` → fork PR → verification → maintainer merge | [live/](live/), or open [akii-codes/mergemind-tests-2.0#1](https://github.com/akii-codes/mergemind-tests-2.0/issues/1) | GitHub Actions |

## neatlogs

- Every trace is tagged: `eval:v1` / `eval:v2` (backtests), `eval:dev-v1` (dev-split diagnosis),
  `eval:faults` (recovery run), `live` (engine). Dashboard: https://app.neatlogs.com.
- One neatlogs session per issue (`<repo>#<number>`); one `issue_event` WORKFLOW span per event with
  `neatlogs.phase_from` / `neatlogs.phase_to`; triage, investigator, verifier, leak-guard and PR-analysis
  spans carry decisions, tool calls, repairs, tokens and prompt version. v2 verifier spans also record
  `neatlogs.normalized` (mechanical fixes applied before the rules).
- Failure-to-fix loop: each v2 change cites the v1 measurement that motivated it
  ([eval/v1-vs-v2.md](eval/v1-vs-v2.md#what-changed-in-v2-and-why-each-from-a-v1-measurement)).
- Prompts are versioned in neatlogs Prompts (`mergemind.investigator`, `mergemind.critic`,
  `mergemind.dup_adjudicator`, `mergemind.pr_reviewer`, `mergemind.leak_critic`; label `production`);
  v2 did not change any prompt.
- Delivery check: 0 export failures, 0 dropped spans (`neatlogs.get_delivery_diagnostics()`).

## Entire

- Runtime: entire-graph (`search`, `def`, `impact`) is MergeMind's code-intelligence backend; v2 runs it
  next to the lexical backend and fuses the two rankings.
- Measured value: the ablation in [eval/locate-v1.md](eval/locate-v1.md) and the fusion gain in
  [eval/locate-v2.md](eval/locate-v2.md).
- Development: `entire enable` (checkpoints for coding sessions) and
  `entire graph init-agents` (agent guide for the graph); both are local dev config and not published.
