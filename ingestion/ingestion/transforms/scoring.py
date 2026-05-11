"""
Sub-score and headline score computation.

Implements spec §9.2 (sub-score aggregation) and §9.3 (headline score).

Sub-scores: equal-weighted mean of smoothed indicator scores within each category.
Headline: weighted sum of sub-scores (weights defined below).
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

# ── Sub-score configuration ───────────────────────────────────────────────────
# Each entry: slug → {weight in headline (sums to 1.0), scored indicators}
# Manheim MUVVI excluded (context_only); BigTicket weights redistributed to 0.25 each.

SUBSCORE_CONFIG: dict[str, dict[str, Any]] = {
    "labor_income": {
        "weight": 0.20,
        "label": "Labor & Income",
        "indicators": [
            "unrate",
            "payems_3mo_avg",
            "ic4wsa",
            "ccsa",
            "real_ahe_yoy",
        ],
    },
    "household_balance_sheet": {
        "weight": 0.15,
        "label": "Household Balance Sheet",
        "indicators": [
            "psavert",
            "real_dpi_yoy",
            "tdsp",
            "networth_dpi_ratio",
        ],
    },
    "credit_stress": {
        "weight": 0.20,
        "label": "Credit Stress",
        "indicators": [
            "drcclacbs",
            "drclacbs",
            "corccacbs",
            "nyfed_serious_delinq",
            "drtsclcc",
        ],
    },
    "spending_demand": {
        "weight": 0.15,
        "label": "Spending & Demand",
        "indicators": [
            "real_pce_mom_ann",
            "rrsfs_yoy",
            "tsa_throughput_vs2019",
            "real_pce_food_svcs_yoy",
        ],
    },
    "sentiment_expectations": {
        "weight": 0.10,
        "label": "Sentiment & Expectations",
        "indicators": [
            "umcsent",
            "cscicp03",
            "nyfed_sce_miss_payment",
        ],
    },
    "inflation_affordability": {
        "weight": 0.10,
        "label": "Inflation & Affordability",
        "indicators": [
            "cpi_yoy",
            "core_cpi_yoy",
            "shelter_cpi_yoy",
            "eia_gas_price",
        ],
    },
    "big_ticket_affordability": {
        "weight": 0.10,
        "label": "Big-Ticket Affordability",
        "indicators": [
            "mortgage30us",
            "new_auto_loan_rate",
            "cc_interest_rate",
            "housing_affordability",
        ],
        # manheim_muvvi excluded (context_only); 4 indicators × 0.25 each = 1.0 within sub-score
    },
}

assert abs(sum(v["weight"] for v in SUBSCORE_CONFIG.values()) - 1.0) < 1e-9, (
    "Sub-score weights must sum to 1.0"
)

# ── Score bands (spec §3.1) ───────────────────────────────────────────────────
SCORE_BANDS = [
    (85, "Very Strong",     "#1a7c3e"),
    (70, "Healthy",         "#2ecc71"),
    (55, "Mixed / Watchful","#f0c419"),
    (40, "Weakening",       "#e67e22"),
    (25, "Stressed",        "#e74c3c"),
    (0,  "Crisis",          "#8b0000"),
]


def score_to_band(score: float) -> tuple[str, str]:
    """Return (label, hex_color) for a headline score."""
    for threshold, label, color in SCORE_BANDS:
        if score >= threshold:
            return label, color
    return "Crisis", "#8b0000"


def compute_subscore(
    indicator_scores: dict[str, float | None],
) -> float | None:
    """
    Equal-weighted mean of available (non-NaN) indicator scores.
    Returns None if no valid scores exist.
    """
    valid = [
        v for v in indicator_scores.values()
        if v is not None and not np.isnan(v)
    ]
    if not valid:
        return None
    return float(np.mean(valid))


def compute_headline(
    subscores: dict[str, float | None],
) -> float | None:
    """
    Weighted sum of sub-scores, re-normalized if any sub-score is missing.

    Re-normalization: if a sub-score is unavailable (NaN / None), its weight
    is redistributed proportionally to the available sub-scores. This means
    early in history (e.g. 1990s before some series existed) the headline
    still computes with whatever sub-scores are available.
    """
    total_weight = 0.0
    weighted_sum = 0.0

    for slug, config in SUBSCORE_CONFIG.items():
        score = subscores.get(slug)
        if score is None or np.isnan(score):
            continue
        weighted_sum += score * config["weight"]
        total_weight += config["weight"]

    if total_weight < 0.01:
        return None

    # Re-normalize to [0, 100] when some weights are missing
    return weighted_sum / total_weight


def compute_all_subscores(
    all_scores_df: pd.DataFrame,
    score_date: str,
) -> dict[str, float | None]:
    """
    Compute all 7 sub-scores for a given date from a wide DataFrame
    of (indicator_slug, date) → smoothed_score.

    all_scores_df: DataFrame with columns [indicator_slug, score_date, smoothed_score]
    score_date: "YYYY-MM-DD" month-start date

    Returns {subscore_slug: score_value}.
    """
    # Filter to the target date
    date_df = all_scores_df[all_scores_df["score_date"] == score_date]
    slug_to_score: dict[str, float | None] = dict(
        zip(date_df["indicator_slug"], date_df["smoothed_score"])
    )

    subscores: dict[str, float | None] = {}
    for subscore_slug, config in SUBSCORE_CONFIG.items():
        ind_scores = {
            slug: slug_to_score.get(slug)
            for slug in config["indicators"]
        }
        subscores[subscore_slug] = compute_subscore(ind_scores)

    return subscores


def compute_deltas(
    headline_df: pd.DataFrame,
    score_date: str,
) -> dict[str, float | None]:
    """
    Compute 1-month, 3-month, 12-month deltas for a headline score date.

    headline_df: DataFrame with columns [score_date, score], sorted chronologically.
    """
    sorted_df = headline_df.sort_values("score_date")
    current_row = sorted_df[sorted_df["score_date"] == score_date]
    if current_row.empty:
        return {"delta_1m": None, "delta_3m": None, "delta_12m": None}

    current_score = float(current_row["score"].iloc[0])
    result: dict[str, float | None] = {}

    for months, key in [(1, "delta_1m"), (3, "delta_3m"), (12, "delta_12m")]:
        # Find the row ~N months earlier
        target_date = pd.Timestamp(score_date) - pd.DateOffset(months=months)
        target_str = target_date.strftime("%Y-%m-%d")
        prior_rows = sorted_df[sorted_df["score_date"] <= target_str]
        if prior_rows.empty:
            result[key] = None
        else:
            prior_score = float(prior_rows.iloc[-1]["score"])
            result[key] = round(current_score - prior_score, 2)

    return result


def compute_biggest_movers(
    subscore_rows: pd.DataFrame,
    score_date: str,
    top_n: int = 3,
) -> tuple[list[dict], list[dict]]:
    """
    Find the biggest subscore gainers and drops vs prior month.

    Returns (gains_list, drops_list) each as [{slug, label, delta, score}].
    subscore_rows: DataFrame with [slug, score_date, score].
    """
    current = subscore_rows[subscore_rows["score_date"] == score_date]
    prior_date = (pd.Timestamp(score_date) - pd.DateOffset(months=1)).strftime("%Y-%m-%d")
    prior = subscore_rows[subscore_rows["score_date"] <= prior_date]
    if prior.empty:
        return [], []
    prior_latest = prior.groupby("slug")["score"].last()

    movers = []
    for _, row in current.iterrows():
        slug = row["slug"]
        label = SUBSCORE_CONFIG.get(slug, {}).get("label", slug)
        prior_score = prior_latest.get(slug)
        if prior_score is None or np.isnan(prior_score):
            continue
        delta = round(float(row["score"]) - float(prior_score), 1)
        movers.append({"slug": slug, "label": label, "delta": delta, "score": round(float(row["score"]), 1)})

    movers.sort(key=lambda x: x["delta"], reverse=True)
    gains = movers[:top_n] if movers else []
    drops = sorted(movers, key=lambda x: x["delta"])[:top_n] if movers else []
    # Only include actual gains/drops
    gains = [m for m in gains if m["delta"] > 0]
    drops = [m for m in drops if m["delta"] < 0]
    return gains, drops
