# neatlogs evidence

Screenshots from MergeMind's neatlogs project, taken on Oct 11, 2026.

| Screenshot | What it shows |
|---|---|
| [01-dashboard.jpg](01-dashboard.jpg) | Project home over 7 days: 702 sessions, 0.4% errors, $0.02 total spend |
| [02-traces-list.png](02-traces-list.png) | Recent traces after the credential mask shipped: inputs show `"nvidia_api_key": "[REDACTED]"`, plus "Slow LLM Response" detections on the two triage runs |
| [03-sessions-per-issue.png](03-sessions-per-issue.png) | One neatlogs session per GitHub issue (`akii-codes/mergemind-…`), each with 6–41 turns across the issue's life |
| [04-trace-triage-spans.png](04-trace-triage-spans.png) | A triage trace of [mergemind-tests-2.0#1](https://github.com/akii-codes/mergemind-tests-2.0/issues/1), the seat double-booking bug: WORKFLOW → `gh.get_issue` TOOL → `localize` CHAIN → `search_code` RETRIEVAL and `symbol_impact` TOOL spans. The settings input shows the key as `[REDACTED]` |
| [05-trace-nemotron-calls.png](05-trace-nemotron-calls.png) | The `investigate` AGENT span: entire-graph retrieval, then five Nemotron 3 Super calls with their token counts (4.9k–13.9k), then the `verify.rules` GUARDRAIL |
| [06-trace-critic-and-report.png](06-trace-critic-and-report.png) | The `verify.critic` GUARDRAIL on Nemotron Lightning (1,029 tokens, $0.0001) and the structured report: bug, accepted, localization confidence 0.95, acceptance criteria, regression-test hint |

## How neatlogs improved MergeMind

- **Before and after runs.** Backtest runs are tagged `eval:v1` and `eval:v2`. Comparing them showed why the agent
  held back good fixes. The fixes that followed cut false safety holds from 25% to 8%, with zero leaks. Numbers:
  [../eval/v1-vs-v2.md](../eval/v1-vs-v2.md).
- **Failure diagnosis.** The `eval:dev-v1` runs on the dev split exposed mechanical verifier rejections, which led
  to the verifier fixes.
- **Recovery runs.** Runs tagged `eval:faults` carry `neatlogs.error_class`, `neatlogs.recovery` and
  `neatlogs.injected_fault` on each span. See [../recovery.md](../recovery.md).
- **A real security bug.** Reading traces revealed that span inputs were exporting MergeMind's own API keys. We
  added a global mask (`mergemind/tracing.py`, passed to `neatlogs.init(mask=...)`), hid the secret fields on the
  settings object, verified `[REDACTED]` in the dashboard (screenshots 02 and 04), and rotated every key.
