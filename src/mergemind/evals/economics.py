"""Unit economics from measured usage → cfo.ai inputs (buildplan-v2 §11).

Cost unit: one triaged issue, and one PR review. Token counts are MEASURED (triage backtest
rows and live PR analyses); prices are public list prices (sources below) and are the only
assumption besides the scenario volumes, which are labelled as such.

cfo.ai (formerly Runway Financial) has no import API, so the output is a drivers CSV and a
markdown model meant to be entered into cfo.ai by a person.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass
from pathlib import Path

# USD per 1M tokens (input, output). Sources: DeepInfra's Nemotron 3 Super pricing post and
# NVIDIA build.nvidia.com via AnotherWrapper (Aug–Sep 2026). The free NVIDIA endpoint is $0.
PRICES = {
    "nvidia/nemotron-3-super-120b-a12b": (0.10, 0.50),
    "nvidia/nemotron-3.5-lightning-30b-a3b": (0.05, 0.20),
    "nvidia/nemotron-3-ultra-550b-a55b": (0.60, 2.40),  # only if used for experiments; not in the default path
}
PRICE_SOURCES = [
    "https://deepinfra.com/blog/nemotron-3-super-provider-pricing-comparison",
    "https://anotherwrapper.com/tools/llm-pricing/nemotron-3.5-lightning-30b-a3b",
]

# Scenario volumes (ASSUMPTIONS, edit in cfo.ai): issues per repo per month
REPO_SIZES = {"small OSS repo": 15, "active OSS repo": 80, "busy repo / internal team": 300}
PR_REVIEWS_PER_ACCEPTED_ISSUE = 2.0  # initial PR + one revision push, each analysed once
ACCEPT_SHARE_FALLBACK = 0.5
HOSTED_COMPUTE_PER_REPO_MONTH = 0.50  # USD: share of a small VM + storage per hosted repo (assumption)


@dataclass
class CostPoint:
    median: float
    p90: float


def cost_of(by_model: dict[str, list[int]]) -> float:
    total = 0.0
    for model, (i, o) in by_model.items():
        pin, pout = PRICES.get(model, PRICES["nvidia/nemotron-3-super-120b-a12b"])
        total += i / 1e6 * pin + o / 1e6 * pout
    return total


def _p(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def triage_costs(rows: list[dict]) -> tuple[CostPoint, dict]:
    fresh = [r for r in rows if not r.get("error") and r.get("by_model") and r.get("in_tok", 0) > 0]
    costs = [cost_of(r["by_model"]) for r in fresh]
    toks = [r["in_tok"] + r["out_tok"] for r in fresh]
    accept = sum(1 for r in rows if r.get("disposition") == "accept") / max(1, sum(1 for r in rows if not r.get("error")))
    detail = {"n": len(fresh), "tokens_median": statistics.median(toks) if toks else None,
              "tokens_p90": _p(toks, 0.9) if toks else None, "accept_share": accept}
    if not costs:
        return CostPoint(float("nan"), float("nan")), detail
    return CostPoint(statistics.median(costs), _p(costs, 0.9)), detail


def pr_costs(store_path: Path, model: str) -> tuple[CostPoint, dict]:
    """PR analyses recorded in the runs table (live sandbox runs)."""
    import sqlite3

    vals = []
    if store_path.exists():
        con = sqlite3.connect(store_path)
        for i, o in con.execute("SELECT in_tok, out_tok FROM runs WHERE kind='pr_analysis' AND in_tok > 0"):
            vals.append(cost_of({model: [i, o]}))
    if not vals:  # measured live on the sandbox kit: ~1.1–1.6k in / 0.6–0.8k out per review (M5 notes)
        vals = [cost_of({model: [1400, 700]})]
    return CostPoint(statistics.median(vals), _p(vals, 0.9)), {"n": len(vals)}


# Business assumptions for the cfo.ai model: example values to replace with your own (not measured).
TIERS = {  # name: (price per month USD, repos per account, issues per repo per month, who pays inference)
    "Free (self-hosted Action, BYO NVIDIA key)": (0, 1, 15, "maintainer"),
    "Pro (hosted, per repo)": (19, 1, 80, "us"),
    "Team (hosted, up to 10 private repos)": (149, 10, 80, "us"),
}
SCENARIOS = {  # name: (new free repos / month, free -> paid conversion / month, monthly churn, LLM price x)
    "Base": (200, 0.02, 0.03, 1.0),
    "Growth": (600, 0.03, 0.02, 1.0),
    "Downside": (80, 0.01, 0.05, 2.0),
}


def entry_sheet(unit_med: float, unit_p90: float, run: str) -> str:
    """Step-by-step sheet for building the business model by hand in cfo.ai (it has no import API)."""
    def tier_cost(repos: int, issues: int, price_x: float = 1.0) -> float:
        return repos * (issues * unit_p90 * price_x + HOSTED_COMPUTE_PER_REPO_MONTH)

    lines = [
        "# cfo.ai entry sheet", "",
        f"Measured inputs come from the `{run}` backtest ([unit_economics.md](unit_economics.md), "
        "[cfo_drivers.csv](cfo_drivers.csv)). Rows marked *example* are business assumptions: replace them with yours. "
        "cfo.ai has no import API, so enter these by hand, in this order (or describe them to cfo.ai's assistant, "
        "Ari, which builds the pages). Built model: https://cfo.ai/s/PG56dy5YWbPcGD_b", "",
        "## 1. Model setup", "",
        "Create a model `MergeMind`, monthly, 24 months, currency USD.", "",
        "## 2. Drivers (inputs)", "",
        "| Driver | Value | Kind |", "|---|---|---|",
        f"| LLM cost per issue, p90 (all-in: triage + PR reviews) | {unit_p90:.4f} | measured |",
        f"| LLM cost per issue, median | {unit_med:.4f} | measured |",
        f"| Hosted compute per paid repo per month | {HOSTED_COMPUTE_PER_REPO_MONTH:.2f} | example |",
    ]
    for name, (price, repos, issues, _) in TIERS.items():
        lines.append(f"| {name}: price / month | {price} | example |")
        lines.append(f"| {name}: repos per account | {repos} | example |")
        lines.append(f"| {name}: issues per repo / month | {issues} | example |")
    lines += ["| Starting cash | your number | input |", "| Team cost / month (salaries, tools) | your number | input |",
              "| Free repos at month 0 | 0 | example |", "",
              "## 3. Formulas (one row each, per month)", "",
              "```",
              "free_repos[t]   = free_repos[t-1] * (1 - churn) + new_free_repos",
              "pro_accounts[t] = pro_accounts[t-1] * (1 - churn) + free_repos[t-1] * conversion * 0.8",
              "team_accounts[t]= team_accounts[t-1] * (1 - churn) + free_repos[t-1] * conversion * 0.2",
              "revenue         = pro_accounts * pro_price + team_accounts * team_price",
              "cogs            = (pro_accounts * pro_repos + team_accounts * team_repos)",
              "                  * (issues_per_repo * llm_cost_per_issue_p90 * llm_price_x + hosted_compute_per_repo)",
              "gross_margin    = (revenue - cogs) / revenue",
              "net_burn        = team_cost + cogs - revenue",
              "cash[t]         = cash[t-1] - net_burn",
              "runway_months   = cash / net_burn            (while net_burn > 0)",
              "```", "",
              "Free repos cost us nothing: the maintainer runs the GitHub Action with their own (free) NVIDIA key.", "",
              "## 4. Scenarios", "",
              "| Scenario | New free repos / month | Conversion / month | Churn / month | LLM price multiplier |",
              "|---|---|---|---|---|"]
    for name, (new, conv, churn, px) in SCENARIOS.items():
        lines.append(f"| {name} | {new} | {conv:.0%} | {churn:.0%} | {px:g}x |")
    lines += ["", "## 5. Check your model against these unit numbers", "",
              "| Tier | Price / month | COGS / month (p90) | Gross margin | COGS at 2x LLM price |", "|---|---|---|---|---|"]
    for name, (price, repos, issues, payer) in TIERS.items():
        if payer != "us":
            lines.append(f"| {name} | $0 | $0 | — | $0 |")
            continue
        c, c2 = tier_cost(repos, issues), tier_cost(repos, issues, 2.0)
        lines.append(f"| {name} | ${price} | ${c:.2f} | {(price - c) / price:.0%} | ${c2:.2f} |")
    lines += ["", "If cfo.ai shows different margins for these tiers, a formula is off. Sensitivities worth a "
              "chart: tokens per issue (model choice, reasoning on/off), LLM price multiplier, conversion, churn."]
    return "\n".join(lines) + "\n"


def build(triage_rows: list[dict], store_path: Path, investigator_model: str, out_dir: Path,
          run: str = "v1") -> str:
    tri, tri_d = triage_costs(triage_rows)
    pr, pr_d = pr_costs(store_path, investigator_model)
    accept = tri_d.get("accept_share") or ACCEPT_SHARE_FALLBACK

    def per_issue(c_tri: float, c_pr: float) -> float:
        return c_tri + accept * PR_REVIEWS_PER_ACCEPTED_ISSUE * c_pr

    unit_med, unit_p90 = per_issue(tri.median, pr.median), per_issue(tri.p90, pr.p90)
    rows = [
        ("triage_cost_per_issue_median_usd", tri.median, "USD", f"measured, n={tri_d['n']} backtest issues"),
        ("triage_cost_per_issue_p90_usd", tri.p90, "USD", "measured"),
        ("triage_tokens_per_issue_median", tri_d.get("tokens_median"), "tokens", "measured"),
        ("triage_tokens_per_issue_p90", tri_d.get("tokens_p90"), "tokens", "measured"),
        ("pr_review_cost_median_usd", pr.median, "USD", f"measured live, n={pr_d['n']}"),
        ("accepted_issue_share", accept, "ratio", "measured (backtest dispositions)"),
        ("pr_reviews_per_accepted_issue", PR_REVIEWS_PER_ACCEPTED_ISSUE, "count", "assumption"),
        ("llm_cost_per_issue_all_in_median_usd", unit_med, "USD", "derived"),
        ("llm_cost_per_issue_all_in_p90_usd", unit_p90, "USD", "derived"),
        ("hosted_compute_per_repo_month_usd", HOSTED_COMPUTE_PER_REPO_MONTH, "USD", "assumption"),
        ("free_tier_marginal_cost_usd", 0.0, "USD", "GitHub Actions on public repos + bring-your-own NVIDIA key"),
    ]
    for name, (pin, pout) in PRICES.items():
        rows.append((f"price_in_per_1M__{name}", pin, "USD", "public list price"))
        rows.append((f"price_out_per_1M__{name}", pout, "USD", "public list price"))
    for label, vol in REPO_SIZES.items():
        rows.append((f"scenario_issues_per_month__{label}", vol, "issues", "assumption"))
        rows.append((f"scenario_cost_per_repo_month__{label}", vol * unit_p90 + HOSTED_COMPUTE_PER_REPO_MONTH,
                     "USD", "derived (p90 cost, conservative)"))

    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "cfo_drivers.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["driver", "value", "unit", "source"])
        for r in rows:
            w.writerow([r[0], "" if r[1] is None else (round(r[1], 6) if isinstance(r[1], float) else r[1]), r[2], r[3]])

    def money(x: float) -> str:
        return f"${x:,.4f}" if x < 1 else f"${x:,.2f}"

    lines = [
        "# MergeMind unit economics (inputs for cfo.ai)", "",
        f"Measured from the {run} backtest (time-travelled triage on real issues) and live PR reviews on the sandbox. "
        "Prices are public list prices for hosted Nemotron (sources at the end). Volumes are assumptions to edit in cfo.ai.",
        "", "## Cost per unit", "",
        "| Unit | Median | p90 |", "|---|---|---|",
        f"| Triage of one issue (investigator + verifier + duplicates + leak guard) | {money(tri.median)} | {money(tri.p90)} |",
        f"| One PR review | {money(pr.median)} | {money(pr.p90)} |",
        f"| **All-in LLM cost per issue** (triage + {accept:.0%} accepted × {PR_REVIEWS_PER_ACCEPTED_ISSUE:g} PR reviews) | "
        f"**{money(unit_med)}** | **{money(unit_p90)}** |",
        "", f"Tokens per triaged issue: median {tri_d.get('tokens_median')}, p90 {tri_d.get('tokens_p90')} "
        f"(n={tri_d['n']}).", "",
        "## Cost per repo per month (conservative: p90 cost per issue)", "",
        "| Scenario (assumed volume) | Issues / month | LLM + compute / month |", "|---|---|---|",
    ]
    for label, vol in REPO_SIZES.items():
        lines.append(f"| {label} | {vol} | {money(vol * unit_p90 + HOSTED_COMPUTE_PER_REPO_MONTH)} |")
    lines += [
        "", "## Segments (buildplan-v2 §11)", "",
        "| Segment | Who pays for inference | Marginal cost to us |", "|---|---|---|",
        "| OSS free (self-hosted GitHub Action, BYO NVIDIA key) | maintainer (free NVIDIA endpoint) | $0 |",
        "| Hosted Pro (per repo, priced by issue volume) | us | the per-repo figures above |",
        "| Team / inner-source (private code, self-hosted Nemotron) | customer GPU or us | GPU-hour based, not per token |",
        "| Enterprise | custom | custom |",
        "", "## How to use in cfo.ai", "",
        "cfo.ai has no import API, so load `cfo_drivers.csv` as a driver table (or type the drivers in), then build: "
        "repos per segment × issues per repo × all-in cost per issue = COGS; price per repo by volume tier = revenue; "
        "team burn → runway. Sensitivities to model: tokens per issue (model choice, reasoning on/off), provider "
        "price, share of free-tier users, conversion.",
        "", "Sources: " + ", ".join(PRICE_SOURCES),
    ]
    md = "\n".join(lines)
    (out_dir / "unit_economics.md").write_text(md, encoding="utf-8")
    (out_dir / "cfo_entry_sheet.md").write_text(entry_sheet(unit_med, unit_p90, run), encoding="utf-8")
    (out_dir / "unit_economics.json").write_text(json.dumps({
        "triage": {"median": tri.median, "p90": tri.p90, **tri_d}, "pr_review": {"median": pr.median, "p90": pr.p90, **pr_d},
        "all_in_per_issue": {"median": unit_med, "p90": unit_p90}, "prices": PRICES}, indent=2), encoding="utf-8")
    return md
