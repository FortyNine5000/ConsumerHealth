"""
Backtest v1 and v2 candidate Consumer Compass signals.

Run after ingestion has populated `headline_scores`,
`headline_signal_components`, and indicator score history:

  python -m ingestion.analytics.backtest --output docs/validation-report.md
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


CANDIDATE_COLUMNS = {
    "v1": "v1_score",
    "stable_v2": "stable_v2_score",
    "turning_point": "turning_point_score",
}


@dataclass(frozen=True)
class OutcomeSpec:
    slug: str
    label: str
    higher_is_better: bool


OUTCOME_SPECS = [
    OutcomeSpec("real_pce_mom_ann", "Forward real PCE momentum", True),
    OutcomeSpec("rrsfs_yoy", "Forward real retail sales YoY", True),
    OutcomeSpec("unrate", "Forward unemployment deterioration", False),
    OutcomeSpec("drcclacbs", "Forward credit-card delinquency deterioration", False),
    OutcomeSpec("corccacbs", "Forward credit-card charge-off deterioration", False),
]


RECESSIONS = [
    ("1990-07-01", "1991-03-01", "1990-91"),
    ("2001-03-01", "2001-11-01", "2001"),
    ("2007-12-01", "2009-06-01", "GFC"),
    ("2020-02-01", "2020-04-01", "COVID"),
]


def _markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    columns = [str(column) for column in df.columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for _, row in df.iterrows():
        values = ["" if pd.isna(row[column]) else str(row[column]) for column in df.columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _corr(a: pd.Series, b: pd.Series) -> float | None:
    frame = pd.concat([a, b], axis=1).dropna()
    if len(frame) < 12:
        return None
    if frame.iloc[:, 0].nunique() < 2 or frame.iloc[:, 1].nunique() < 2:
        return None
    value = frame.iloc[:, 0].corr(frame.iloc[:, 1])
    if value is None or pd.isna(value):
        return None
    return float(value)


def compute_forward_outcome_metrics(
    candidate_df: pd.DataFrame,
    outcomes_df: pd.DataFrame,
    outcome_specs: list[OutcomeSpec] | None = None,
    horizon_months: int = 6,
    warning_threshold: float = -10.0,
) -> pd.DataFrame:
    """
    Score candidates against forward outcome changes.

    `forward_change_good` is positive when the future outcome improved and
    negative when it deteriorated, regardless of the raw outcome direction.
    """
    outcome_specs = outcome_specs or OUTCOME_SPECS
    rows: list[dict[str, Any]] = []

    candidates = candidate_df.copy()
    candidates["score_date"] = pd.to_datetime(candidates["score_date"])
    candidates = candidates.set_index("score_date").sort_index()

    outcomes = outcomes_df.copy()
    outcomes["score_date"] = pd.to_datetime(outcomes["score_date"])
    outcomes = outcomes.set_index("score_date").sort_index()

    for spec in outcome_specs:
        if spec.slug not in outcomes.columns:
            continue
        raw = pd.to_numeric(outcomes[spec.slug], errors="coerce")
        forward_change = raw.shift(-horizon_months) - raw
        if not spec.higher_is_better:
            forward_change = -forward_change

        for candidate_name, column in CANDIDATE_COLUMNS.items():
            if column not in candidates.columns:
                continue
            score = pd.to_numeric(candidates[column], errors="coerce")
            score_delta_3m = score.diff(3)
            aligned = pd.concat([score_delta_3m, forward_change], axis=1).dropna()
            warnings = aligned[aligned.iloc[:, 0] <= warning_threshold]
            hit_rate = None
            false_warnings = None
            if len(warnings) > 0:
                hits = (warnings.iloc[:, 1] < 0).sum()
                hit_rate = float(hits / len(warnings))
                false_warnings = int(len(warnings) - hits)

            rows.append({
                "candidate": candidate_name,
                "outcome_slug": spec.slug,
                "outcome_label": spec.label,
                "horizon_months": horizon_months,
                "corr_score_to_forward_change": _corr(score, forward_change),
                "corr_3m_score_change_to_forward_change": _corr(score_delta_3m, forward_change),
                "warning_count": int(len(warnings)),
                "warning_hit_rate": hit_rate,
                "false_warning_count": false_warnings,
            })

    return pd.DataFrame(rows)


def compute_recession_metrics(candidate_df: pd.DataFrame) -> pd.DataFrame:
    """Measure candidate score deterioration before and during known recessions."""
    candidates = candidate_df.copy()
    candidates["score_date"] = pd.to_datetime(candidates["score_date"])
    candidates = candidates.set_index("score_date").sort_index()

    rows: list[dict[str, Any]] = []
    for recession_start, recession_trough, label in RECESSIONS:
        start = pd.Timestamp(recession_start)
        trough = pd.Timestamp(recession_trough)

        for candidate_name, column in CANDIDATE_COLUMNS.items():
            if column not in candidates.columns:
                continue
            score = pd.to_numeric(candidates[column], errors="coerce").dropna()
            if score.empty:
                continue

            at_start = score[score.index <= start]
            pre_3m = score[score.index <= start - pd.DateOffset(months=3)]
            pre_6m = score[score.index <= start - pd.DateOffset(months=6)]
            in_recession = score[(score.index >= start) & (score.index <= trough)]
            if at_start.empty or in_recession.empty:
                continue

            start_score = float(at_start.iloc[-1])
            trough_score = float(in_recession.min())
            rows.append({
                "candidate": candidate_name,
                "recession": label,
                "score_at_start": start_score,
                "drop_3m_before_start": (
                    None if pre_3m.empty else float(pre_3m.iloc[-1] - start_score)
                ),
                "drop_6m_before_start": (
                    None if pre_6m.empty else float(pre_6m.iloc[-1] - start_score)
                ),
                "drawdown_start_to_trough": float(start_score - trough_score),
            })

    return pd.DataFrame(rows)


def render_validation_markdown(
    forward_metrics: pd.DataFrame,
    recession_metrics: pd.DataFrame,
    generated_at: str | None = None,
) -> str:
    generated = generated_at or pd.Timestamp.utcnow().strftime("%Y-%m-%d")

    lines = [
        "# Consumer Compass Signal Validation",
        "",
        f"Generated: {generated}",
        "",
        "This report compares the production v1 score with two research candidates:",
        "",
        "- `stable_v2`: 60% state, 25% short momentum, 15% medium trend.",
        "- `turning_point`: 40% state, 40% short momentum, 20% medium trend.",
        "",
        "## Forward Outcome Fit",
        "",
    ]

    if forward_metrics.empty:
        lines.append("No forward outcome metrics were available.")
    else:
        table = forward_metrics.copy()
        for column in [
            "corr_score_to_forward_change",
            "corr_3m_score_change_to_forward_change",
            "warning_hit_rate",
        ]:
            table[column] = table[column].map(
                lambda v: "" if v is None or pd.isna(v) else f"{v:.2f}"
            )
        lines.append(_markdown_table(table))

    lines.extend(["", "## Recession Response", ""])
    if recession_metrics.empty:
        lines.append("No recession metrics were available.")
    else:
        table = recession_metrics.copy()
        for column in [
            "score_at_start",
            "drop_3m_before_start",
            "drop_6m_before_start",
            "drawdown_start_to_trough",
        ]:
            table[column] = table[column].map(
                lambda v: "" if v is None or pd.isna(v) else f"{v:.1f}"
            )
        lines.append(_markdown_table(table))

    lines.extend([
        "",
        "## Reading The Results",
        "",
        "Higher forward correlations are better. A useful weakening signal should also have "
        "a high warning hit rate and a low false-warning count when its 3-month score change "
        "falls by at least 10 points.",
    ])
    return "\n".join(lines) + "\n"


async def fetch_backtest_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch candidate scores and outcome series from Turso."""
    from ingestion.db import Statement, _make_client

    client = _make_client()
    try:
        headline = await client.execute(
            """
            SELECT h.score_date,
                   h.score AS v1_score,
                   hs.stable_v2_score,
                   hs.turning_point_score
            FROM headline_scores h
            LEFT JOIN headline_signal_components hs ON hs.score_date = h.score_date
            ORDER BY h.score_date
            """
        )
        candidate_df = pd.DataFrame(
            headline.rows,
            columns=["score_date", "v1_score", "stable_v2_score", "turning_point_score"],
        )

        slugs = [spec.slug for spec in OUTCOME_SPECS]
        placeholders = ",".join("?" for _ in slugs)
        result = await client.execute(
            Statement(
                f"""
                SELECT i.slug, sc.score_date, sc.raw_value
                FROM indicator_scores sc
                JOIN indicators i ON i.id = sc.indicator_id
                WHERE i.slug IN ({placeholders})
                ORDER BY sc.score_date
                """,
                slugs,
            )
        )
        outcome_long = pd.DataFrame(result.rows, columns=["slug", "score_date", "raw_value"])
        if outcome_long.empty:
            return candidate_df, pd.DataFrame(columns=["score_date", *slugs])
        outcome_df = (
            outcome_long.pivot_table(
                index="score_date",
                columns="slug",
                values="raw_value",
                aggfunc="last",
            )
            .reset_index()
            .rename_axis(None, axis=1)
        )
        return candidate_df, outcome_df
    finally:
        await client.close()


async def run(output: str | None = None) -> str:
    candidate_df, outcomes_df = await fetch_backtest_inputs()
    forward = compute_forward_outcome_metrics(candidate_df, outcomes_df)
    recession = compute_recession_metrics(candidate_df)
    markdown = render_validation_markdown(forward, recession)
    if output:
        Path(output).write_text(markdown)
    return markdown


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest Consumer Compass signal candidates")
    parser.add_argument("--output", help="Optional markdown output path")
    args = parser.parse_args()
    markdown = asyncio.run(run(output=args.output))
    if not args.output:
        print(markdown)


if __name__ == "__main__":
    main()
