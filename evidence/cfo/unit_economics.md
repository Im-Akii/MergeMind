# MergeMind unit economics (inputs for cfo.ai)

Measured from the v1 backtest (time-travelled triage on real issues) and live PR reviews on the sandbox. Prices are public list prices for hosted Nemotron (sources at the end). Volumes are assumptions to edit in cfo.ai.

## Cost per unit

| Unit | Median | p90 |
|---|---|---|
| Triage of one issue (investigator + verifier + duplicates + leak guard) | $0.0079 | $0.0118 |
| One PR review | $0.0004 | $0.0005 |
| **All-in LLM cost per issue** (triage + 50% accepted × 2 PR reviews) | **$0.0084** | **$0.0123** |

Tokens per triaged issue: median 69380.5, p90 86962 (n=18).

## Cost per repo per month (conservative: p90 cost per issue)

| Scenario (assumed volume) | Issues / month | LLM + compute / month |
|---|---|---|
| small OSS repo | 15 | $0.6848 |
| active OSS repo | 80 | $1.49 |
| busy repo / internal team | 300 | $4.20 |

## Segments (buildplan-v2 §11)

| Segment | Who pays for inference | Marginal cost to us |
|---|---|---|
| OSS free (self-hosted GitHub Action, BYO NVIDIA key) | maintainer (free NVIDIA endpoint) | $0 |
| Hosted Pro (per repo, priced by issue volume) | us | the per-repo figures above |
| Team / inner-source (private code, self-hosted Nemotron) | customer GPU or us | GPU-hour based, not per token |
| Enterprise | custom | custom |

## How to use in cfo.ai

cfo.ai has no import API, so load `cfo_drivers.csv` as a driver table (or type the drivers in), then build: repos per segment × issues per repo × all-in cost per issue = COGS; price per repo by volume tier = revenue; team burn → runway. Sensitivities to model: tokens per issue (model choice, reasoning on/off), provider price, share of free-tier users, conversion.

Sources: https://deepinfra.com/blog/nemotron-3-super-provider-pricing-comparison, https://anotherwrapper.com/tools/llm-pricing/nemotron-3.5-lightning-30b-a3b