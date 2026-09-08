# Post-Race Review report draft input — 2026-R13 Italian Grand Prix

This is a factual input sheet for a writing agent, not a polished final report.
Numerical JSON in this directory is authoritative. It was selected from the
immutable Post-Race run `20260908T215427.626629Z-dfe089c9e0`. Its manifest is
`PARTIAL` solely because Event-Aware Hindsight Optimum is `UNAVAILABLE`; every
currently implemented output succeeded.

## Key findings and numbers

- The causal replay contains 35 leader-lap updates. At leader lap 17, MEDIUM is
  directly informed at `0.215040 s/lap`; HARD is derived at `0.144507 s/lap`;
  and SOFT is derived from MEDIUM at `0.229549 s/lap`.
- By the final update (leader lap 53), HARD is directly informed at
  `0.033666 s/lap`, MEDIUM is directly informed at `0.085392 s/lap`, and SOFT
  remains derived at `0.091153 s/lap`. SOFT is never represented as directly
  observed. Its final value preserves the implemented SOFT:MEDIUM pre-race
  degradation ratio of `1.067471`.
- Final live-model support is 196 clean race laps across 23 runs. Effective
  sample size is `2802.05` (`0.0350` of 80,000 samples), weighted RMSE is
  `0.710560 s`, and numerical status is adequate.
- Full-race Retro estimates are separate from the live replay: HARD
  `0.033241 s/lap`, MEDIUM `0.075392 s/lap`, and SOFT `0.245204 s/lap`.
  These are estimates, not ground truth.
- The green-race optimum ties `H-(35)M` and `M-(18)H` at model cost
  `72.245301`. It uses the empirical green pit loss of `30.28125 s` and does
  not use the actual SC/VSC event timeline.
- Empirical pit-loss medians have small and uneven support: green `30.28125 s`
  from 2 samples and SC/VSC `37.2315 s` from 7 samples. Treat the counterintuitive
  SC/VSC comparison as a sample/context limitation, not a general conclusion.
- The included 2026 performance tracker extends through R13. It is useful
  supporting context only and is not suitable as the public-facing hero figure;
  the public `woStrategy` season tracker remains the validated R1-R12 version.

## Suggested figures

1. `figures/live_mc_degradation_evolution.png` — primary corrected tyre figure;
   dashed values are derived and solid values are directly informed.
2. `figures/live_mc_model_quality_evolution.png` — sampler/support qualification.
3. `figures/race_performance_tracker_team_baseline.png` — optional supporting
   R13 context only, not a public-facing hero figure.

## Limitations to retain

- This is historical `algorithm_only` replay validation, not evidence of live
  operational use by a team.
- Unsupported compounds must not be described as directly observed. In this
  replay SOFT remains derived from MEDIUM; HARD becomes direct only once data
  support is available.
- Apparent curve stability does not establish physical identifiability. Preserve
  effective-sample-size and RMSE qualifications alongside point estimates.
- Retro Green Optimum is a green-race counterfactual and omits the actual event
  timeline. Event-Aware Hindsight Optimum is explicitly `UNAVAILABLE` because a
  deterministic SC/VSC counterfactual API has not been extracted from
  `woPlanner`; do not infer or fabricate it.

## Source artifacts

- `manifest.json`, `event.metadata.json`, `config.resolved.json` — run identity,
  event context, component statuses and resolved inputs.
- `report_context.json`, `analysis_results.json` — report-facing context and the
  complete workflow result payload.
- `live_mc_history.json` — causal update history, source labels, support and
  numerical diagnostics for the corrected tyre figure.
- `retro_tyre_estimate.json`, `tyre_comparison.json` — full-race Retro estimates
  and their comparison with the pre-race prediction.
- `retro_green_optimum.json` — green-race exact-search result and limitations.
- `empirical_pit_loss.json` — empirical pit-loss components and sample counts.
- `race_performance_tracker.json` — R1-R13 supporting performance context.
