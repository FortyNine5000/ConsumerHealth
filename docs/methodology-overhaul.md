# Methodology Overhaul Notes

This note turns the Perplexity starter feedback into a concrete v2 methodology plan for The Consumer Compass.

## What the current methodology already does well

- Uses free, citable data sources and keeps the score reproducible.
- Avoids look-ahead bias with expanding-window percentile ranks.
- Applies economic transforms before scoring for key flow indicators, including YoY growth, 3-month annualized momentum, and CPI distance from 2%.
- Publishes headline 1-month, 3-month, and 12-month deltas.
- Separates the headline score into seven interpretable sub-scores.

## Main gap

The current production score is mainly a state score. It answers, "How healthy is this reading versus its own prior history?" That is useful, but it does not make enough room for two investor-relevant questions:

- Is the signal getting better or worse over the last 1-3 releases?
- Is that change persistent over the last 6-12 months?

The fix should not be to make the headline score hyper-reactive. A consumer health index needs both stability and turning-point sensitivity. The better architecture is to compute state, short momentum, and medium trend separately, then choose a headline mix only after backtesting.

## May 2026 quality-control fixes

Before changing the headline formula, v1 needs two mechanical corrections:

- Build a monthly score panel before sub-score and headline aggregation, carrying daily, weekly, monthly, and quarterly data forward only inside an explicit freshness window.
- Avoid headline re-normalization around only the fastest-updating indicators during a partial current month.
- Normalize the FRED Z.1 household net-worth series to the same dollar scale as disposable personal income before calculating net worth / DPI.

## Recommended v2 signal stack

| Layer | Purpose | Candidate computation | Initial weight range |
| --- | --- | --- | --- |
| State | Current consumer health | Existing direction-adjusted expanding percentile | 50-65% |
| Short momentum | Fresh deterioration or improvement | 1-3 release change in score or transformed raw value, normalized by historical move size | 20-35% |
| Medium trend | Persistence of the move | 6-12 month slope, 3m vs 12m average, or cumulative change | 10-20% |

Perplexity's suggested 40% state / 40% short change / 20% medium trend is a useful aggressive candidate, but likely too twitchy for the public headline score. It should be tested as a "turning-point" or "risk" variant against more stable mixes such as 60/25/15 and 55/30/15.

## Indicator-level design

For each scored indicator, keep these fields conceptually distinct:

- `raw_value`: the upstream value.
- `transformed_value`: the economically meaningful version, such as YoY growth or debt-service ratio.
- `state_score`: percentile or robust z-score of transformed value.
- `momentum_score`: normalized recent change, frequency-aware.
- `trend_score`: normalized 6-12 month persistence.
- `staleness_days`: days since the last source observation or release.
- `revision_risk`: qualitative or numeric flag for series with large historical revisions.

The existing schema stores `raw_value`, percentile rank, score, and smoothed score. A v2 schema can either extend `indicator_scores` or add an `indicator_signal_components` table.

## Weighting improvements to test

Keep the seven public sub-score weights stable at first. Users already understand them, and changing them at the same time as the scoring formula would make the impact hard to interpret.

Inside each sub-score, test three alternatives:

- Equal weight: current baseline.
- Leading tilt: leading indicators get 1.2x, coincident get 1.0x, lagging get 0.8x, then weights renormalize.
- Reliability tilt: indicators with cleaner history and lower revision risk get modestly higher weight.

Do not ship a tilt unless it improves out-of-sample validation and remains explainable.

## Validation plan

The validation report should use only information available as of each historical date.

Core tests:

- Recession response: score deterioration before or by NBER recession starts.
- Outcome fit: correlation and directional accuracy versus 3-6 month forward real PCE, real retail sales, unemployment changes, delinquencies, and charge-offs.
- False positives: periods where the score weakened sharply but forward consumer outcomes did not.
- Lead time: whether the v2 score catches turns earlier than v1.
- Noise: whether added momentum increases month-to-month churn without better outcome fit.
- Segment stress: whether credit and affordability signals capture lower-income stress earlier than aggregate spending data.

Recommended publishable metrics:

- Average score change 3 and 6 months before recessions.
- Hit rate for forward real PCE slowdown.
- False-warning count by expansion.
- Maximum drawdown in known consumer stress periods.
- Difference between v1 and v2 at historical turning points.

## UI / product improvements

- Show each headline score as `Level`, `Momentum`, and `Trend`, not only a single 0-100 number.
- Add indicator-level 1m / 3m / 12m movement next to the raw value.
- Add a staleness badge for quarterly or delayed indicators.
- Add a "what changed" panel that cites the specific indicators dragging the score up or down.
- Keep sub-score filtering on the homepage and add a component breakdown so users can see whether a sub-score is weak because of level, momentum, or trend.
- Keep user-customizable weights as a later feature; first make the default methodology defensible.

## Suggested implementation order

1. Add pure Python helper functions for momentum and trend scores with unit tests.
2. Generate state, momentum, and trend components for every indicator without changing the headline score.
3. Store the components in a new table or JSON column and expose them on indicator pages.
4. Build a backtest script comparing v1 against candidate v2 mixes.
5. Publish a validation report under `docs/` and link it from `/methodology`.
6. Switch the headline only after selecting the best candidate mix.

## Open choices

- Whether momentum should be computed from transformed raw values or from already-normalized state scores.
- Whether the headline should use one default mix or publish two scores: stable health and turning-point risk.
- Whether to keep expanding percentile as the only scaler or add robust z-scores for severity diagnostics.
- How to handle extreme pandemic-era values so they do not permanently compress later percentile variation.
- How much to penalize stale quarterly data versus simply labeling it.
