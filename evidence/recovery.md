# Failure recovery evidence (buildplan-v2 §13, M7)

Every failure class has a defined recovery, and each is exercised by a test or a live run.
Live runs are traced in neatlogs with the tag `eval:faults`; spans carry `neatlogs.error_class`,
`neatlogs.recovery` and `neatlogs.injected_fault`.

## Live run: four injected faults, one real triage (pallets/click#3362)

```
MERGEMIND_FAULTS="gh.get_issue:500@1,llm.api:429@1,llm.investigate:bad_json@2,graph.search:timeout@1" \
MERGEMIND_EVAL_RUN=faults uv run mergemind triage https://github.com/pallets/click/issues/3362
```

| Injected fault | Recovery | Observed |
|---|---|---|
| GitHub 500 on reading the issue | exponential backoff + retry in the client | issue read on retry |
| Nemotron 429 on the first call | backoff + retry | call succeeded on retry |
| malformed tool-call JSON from the investigator | error message returned to the model, counted as a repair | `repairs=1`, model re-issued a valid call |
| entire-graph search timeout | lexical (git grep) fallback | `entire search failed …; lexical fallback` |
| **Outcome** | | **ACCEPT, verifier pass**, same root cause as the clean run |

Log: [eval/faults-v1.log](eval/faults-v1.log)

## Scenario tests (deterministic)

| Scenario | Failure | Recovery | Test |
|---|---|---|---|
| S16a | GitHub 429 when merging | durable timer until the rate-limit reset; merge retried and succeeds | `tests/test_merge_gate.py::test_S16a_rate_limited_merge_retries_at_reset` |
| S16b | GitHub 403 when inviting a contributor | PERMISSION_BLOCKED, card explains, maintainers told, no retry | `tests/test_access_flow.py::test_S16b_permission_denied_invite_blocks_without_retry` |
| — | 403 on any write | PERMISSION_BLOCKED, no retry | `tests/test_loop.py::test_permission_error_blocks_without_retry` |
| — | 429 on any write | retry timer at the reset time | `tests/test_loop.py::test_rate_limit_schedules_retry_timer` |
| — | malformed tool call | error reply + repair | `tests/test_triage_scenarios.py::test_malformed_tool_call_is_repaired` |
| — | investigator over budget | forced final structured report | `tests/test_triage_scenarios.py::test_budget_exhausted_forces_final_report` |
| — | invalid LLM JSON | one repair turn, then escalate | `tests/test_basics.py::test_structured_repairs_invalid_json` |
| — | leak critic unavailable | fail closed: hold for a maintainer | `tests/test_leak_guard.py::test_critic_outage_fails_closed` |
| S11 | push after approval | approval voided, re-verification, comment explains | `tests/test_merge_gate.py::test_S11_push_after_approval_voids_it` |
| S12 | base branch moved after approval | branch updated by GitHub, re-verified, approval carried only if nothing else changed | `tests/test_merge_gate.py::test_S12_base_moved_updates_branch_and_keeps_approval` |
| S14 | default-branch CI red after merge | revert PR opened (never merged by MergeMind), maintainers told | `tests/test_merge_gate.py::test_S14_red_default_branch_opens_revert_pr` |
| — | CI never reports | hold for maintainer after `pr.ci_timeout` | `tests/test_pr_flow.py::test_ci_never_reports_times_out_to_maintainer` |
