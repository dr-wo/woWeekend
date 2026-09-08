# Race Preparation report draft input — 2026-R13 Italian Grand Prix

This is a factual input sheet for a writing agent, not a polished final report.
Numerical JSON in this directory is authoritative. It was selected from the
immutable Race Preparation run `20260908T210451.341404Z-347bfbf4c7`, whose
manifest status is `SUCCESS`.

## Key findings and numbers

- The production tyre input is the historical Race-Retro P0/D0 baseline trained
  through R12 and mapped to the R13 allocation. No manual fallback overrides
  were applied, and current-weekend FP evidence did not modify production.
- Effective degradation is HARD `0.150340 s/lap`, MEDIUM `0.223719 s/lap`, and
  SOFT `0.238814 s/lap`. Performance delta to MEDIUM is HARD `+0.333745 s`,
  MEDIUM `0.000000 s`, and SOFT `-0.319645 s`.
- In the rules-compliant 53-lap envelope, the predicted MEDIUM degradation is
  above the 1-to-2-stop cutoff (`0.100273 s/lap`) and below the 2-to-3-stop
  cutoff (`0.252840 s/lap`), so the model selects the two-stop region.
- The first three exact-search rows tie at cost `139.674416`: `H-(19)H-38(S)`,
  `H-(19)S-34(H)`, and `S-(15)H-34(H)`. The leading three-stop row,
  `H-(16)S-28(S)-40(S)`, is `2.271896 s` behind in this model.
- Diagnostic MEDIUM FP coordinates are FP1 `0.057554 s/lap` (44 laps, 8 runs),
  FP2 `0.099784 s/lap` (57 laps, 10 runs), and FP3 `0.063075 s/lap`
  (8 laps, 2 runs). They are reference markers only.

## Suggested figures

1. `figures/cutoff_rules_compliant.png` — primary figure for the rules-compliant
   stop-count conclusion and FP markers.
2. `figures/cutoff_unrestricted.png` — optional sensitivity/context figure; do
   not substitute its unrestricted result for the rules-compliant conclusion.

## Limitations to retain

- Strategy costs are conditional on the configured 53-lap distance, tyre model,
  compound relationship and pit-loss assumptions; they are not race forecasts.
- The strategy search uses central tyre values even though tyre uncertainty is
  retained elsewhere in the input artifacts.
- FP absolute degradation is structurally confounded with fuel/load effects,
  compound performance is not independently identifiable, and FP3 has only two
  usable runs. FP values must remain labelled diagnostic-only.
- The P0/D0 production prediction is historical and trained only through R12.

## Source artifacts

- `manifest.json`, `event.metadata.json`, `config.resolved.json` — run identity,
  event context, status and resolved inputs.
- `report_context.json`, `analysis_results.json` — report-facing context and the
  complete workflow result payload.
- `tyre_prediction.effective.json` — effective production values and provenance.
- `practice_tyre_evidence.json` — FP support, identifiability and limitations.
- `strategy_rules_compliant.json` — ranked exact-search rows.
- `cutoff_rules_compliant.json`, `cutoff_unrestricted.json` — envelope data,
  cutoffs, diagnostic markers and warnings.
