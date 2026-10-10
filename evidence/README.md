# Evidence index

| What | Where | Tool used |
|---|---|---|
| Backtest v1 summary and v2 plan | [eval/summary-v1.md](eval/summary-v1.md) | — |
| Localization backtest, 450 issues, baselines + entire-graph ablation | [eval/locate-v1.md](eval/locate-v1.md), [eval/locate-v1.json](eval/locate-v1.json) | Entire (entire-graph) |
| Full Nemotron triage on a test-split sample | [eval/triage-v1.json](eval/triage-v1.json), log [eval/triage-v1.log](eval/triage-v1.log) | NVIDIA Nemotron, neatlogs |
| PR safety / scope backtest on merged fix PRs | [eval/pr-static-v1.json](eval/pr-static-v1.json) | — |
| Failure recovery (live fault injection + scenario tests) | [recovery.md](recovery.md), log [eval/faults-v1.log](eval/faults-v1.log) | neatlogs (`eval:faults`) |
| Leak guard corpus | `uv run mergemind eval-leak --critic` (0 leaks, 0 false redactions) | Nemotron Lightning |
| Unit economics → cfo.ai | [cfo/unit_economics.md](cfo/unit_economics.md), [cfo/cfo_drivers.csv](cfo/cfo_drivers.csv) | cfo.ai (manual import) |

## neatlogs

- Every trace is tagged: `eval:v1` (backtests), `eval:faults` (recovery run), `live` (engine).
- One neatlogs session per issue (`<repo>#<number>`); one `issue_event` WORKFLOW span per event with
  `neatlogs.phase_from` / `neatlogs.phase_to`; triage, investigator, verifier, leak-guard and PR-analysis
  spans carry decisions, tool calls, repairs, tokens and prompt version.
- Prompts are versioned in neatlogs Prompts (`mergemind.investigator`, `mergemind.critic`,
  `mergemind.dup_adjudicator`, `mergemind.pr_reviewer`, `mergemind.leak_critic`; label `production`).
- Delivery check: 0 export failures, 0 dropped spans (`neatlogs.get_delivery_diagnostics()`).

## Entire

- Runtime: entire-graph (`search`, `def`, `impact`) is MergeMind's code-intelligence backend.
- Measured value: the ablation in [eval/locate-v1.md](eval/locate-v1.md) (entire-graph vs lexical).
- Development: `entire enable --agent claude-code` (checkpoints for agent sessions) and
  `entire graph init-agents` (AGENTS.md / CLAUDE.md).
