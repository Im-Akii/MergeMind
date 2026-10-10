# cfo.ai entry sheet

Measured inputs come from the `v2` backtest ([unit_economics.md](unit_economics.md), [cfo_drivers.csv](cfo_drivers.csv)). Rows marked *example* are business assumptions: replace them with yours. cfo.ai has no import API, so enter these by hand, in this order (or describe them to cfo.ai's assistant, Ari, which builds the pages). Built model: https://cfo.ai/s/PG56dy5YWbPcGD_b

## 1. Model setup

Create a model `MergeMind`, monthly, 24 months, currency USD.

## 2. Drivers (inputs)

| Driver | Value | Kind |
|---|---|---|
| LLM cost per issue, p90 (all-in: triage + PR reviews) | 0.0115 | measured |
| LLM cost per issue, median | 0.0074 | measured |
| Hosted compute per paid repo per month | 0.50 | example |
| Free (self-hosted Action, BYO NVIDIA key): price / month | 0 | example |
| Free (self-hosted Action, BYO NVIDIA key): repos per account | 1 | example |
| Free (self-hosted Action, BYO NVIDIA key): issues per repo / month | 15 | example |
| Pro (hosted, per repo): price / month | 19 | example |
| Pro (hosted, per repo): repos per account | 1 | example |
| Pro (hosted, per repo): issues per repo / month | 80 | example |
| Team (hosted, up to 10 private repos): price / month | 149 | example |
| Team (hosted, up to 10 private repos): repos per account | 10 | example |
| Team (hosted, up to 10 private repos): issues per repo / month | 80 | example |
| Starting cash | your number | input |
| Team cost / month (salaries, tools) | your number | input |
| Free repos at month 0 | 0 | example |

## 3. Formulas (one row each, per month)

```
free_repos[t]   = free_repos[t-1] * (1 - churn) + new_free_repos
pro_accounts[t] = pro_accounts[t-1] * (1 - churn) + free_repos[t-1] * conversion * 0.8
team_accounts[t]= team_accounts[t-1] * (1 - churn) + free_repos[t-1] * conversion * 0.2
revenue         = pro_accounts * pro_price + team_accounts * team_price
cogs            = (pro_accounts * pro_repos + team_accounts * team_repos)
                  * (issues_per_repo * llm_cost_per_issue_p90 * llm_price_x + hosted_compute_per_repo)
gross_margin    = (revenue - cogs) / revenue
net_burn        = team_cost + cogs - revenue
cash[t]         = cash[t-1] - net_burn
runway_months   = cash / net_burn            (while net_burn > 0)
```

Free repos cost us nothing: the maintainer runs the GitHub Action with their own (free) NVIDIA key.

## 4. Scenarios

| Scenario | New free repos / month | Conversion / month | Churn / month | LLM price multiplier |
|---|---|---|---|---|
| Base | 200 | 2% | 3% | 1x |
| Growth | 600 | 3% | 2% | 1x |
| Downside | 80 | 1% | 5% | 2x |

## 5. Check your model against these unit numbers

| Tier | Price / month | COGS / month (p90) | Gross margin | COGS at 2x LLM price |
|---|---|---|---|---|
| Free (self-hosted Action, BYO NVIDIA key) | $0 | $0 | — | $0 |
| Pro (hosted, per repo) | $19 | $1.42 | 93% | $2.34 |
| Team (hosted, up to 10 private repos) | $149 | $14.21 | 90% | $23.43 |

If cfo.ai shows different margins for these tiers, a formula is off. Sensitivities worth a chart: tokens per issue (model choice, reasoning on/off), LLM price multiplier, conversion, churn.
