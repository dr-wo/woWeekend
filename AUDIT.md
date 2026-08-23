# woWeekend capability audit

This audit was completed before implementation. `woWeekend` will orchestrate these
capabilities and will not duplicate their numerical methods.

| Capability | Existing location/API | Current owner | Reuse status | Required change |
|---|---|---|---|---|
| FP qualifying lap roles, out/in/cool durations and track progression | `woStrategy/src/wostrategy/analysis/pre_quali.py`: `analyze_pre_quali()`, `estimate_push_sequence_track_progression()`; `woPlanner/src/woplanner/quali/analysis.py`: `QualiAnalysisService` | woStrategy calculation, woPlanner orchestration | `REUSE_AS_IS` | Call the woStrategy API from the qualifying workflow and persist its small outputs. |
| Live Q track estimate and manual override | `woPlanner/src/woplanner/quali/service.py`: `QualiLiveService._update_track_from_part()`, `set_track_override()`, `transition_to()` | woPlanner | `EXTRACT_PUBLIC_API` | Keep GUI/manual state in woPlanner; expose pure Q-only estimate/snapshot contracts for non-GUI orchestration. No FP laps enter the live-Q estimate. |
| Qualifying performance review | `woStrategy/src/wostrategy/analysis/quali_performance.py`: `QualiPerformance`, `calculate_quali_performance()`; script adapter in `script/quali_performance_tracker.py` | woStrategy | `REUSE_AS_IS` | Add only an orchestration adapter if CLI-shaped loading is needed; pass supported overrides unchanged. |
| Qualifying track-evolution model | `woStrategy/src/wostrategy/model/track_evolution.py`: `fit_compound_track_evolution()` and model implementations | woStrategy | `REUSE_AS_IS` | None. |
| Pre-race tyre cache/pipeline | `woStrategy/src/wostrategy/analysis/tyre_prediction_pipeline.py`: versioned prediction JSON under `tyre_prediction/.../predictions/round=*/versions`; `run_tyre_prediction_pipeline()` | woStrategy | `EXTRACT_PUBLIC_API` | Add a provider-neutral `get_pre_race_tyre_prediction()` API and stable MEDIUM-reference schema; keep current cache format private to provider. |
| FP tyre evidence/new fusion work | `woStrategy/src/wostrategy/analysis/fp_tyre_evidence.py` and weekend model artifacts | woStrategy | `EXTEND_EXISTING` | Do not introduce Sprint weighting. Future provider may replace the current provider behind the stable schema. |
| Strategy optimiser | `woStrategy/src/wostrategy/algorithm/exact_strategy_search.py`: `search_best_compound_sequences()`, `optimise_same_sequence()` | woStrategy | `REUSE_AS_IS` | Add general `StrategyRules` filtering and fixed-stop-count envelope APIs in woStrategy; do not implement optimisation in woWeekend. |
| Current strategy orchestration/UI | `woPlanner/src/woplanner/analysis/service.py`: `AnalysisService.generate_continuations()`; controls in `analysis/gui.py` | woPlanner | `EXTEND_EXISTING` | Later GUI integration can pass `StrategyRules`; existing default remains unrestricted. |
| Retro Monte Carlo and diagnostics | `woStrategy/src/wostrategy/analysis/race_performance_review.py`, `algorithm/monte_carlo_race_performance.py`, `model/live_retro_performance.py` | woStrategy | `REUSE_AS_IS` | Workflow adapter must retain uncertainty, RMSE and identifiability metadata and must call results estimates, not ground truth. |
| Race performance review | `woStrategy/src/wostrategy/analysis/race_performance_review.py`: `calculate_monte_carlo_race_performance_review()`; script adapter `script/race_performance_review.py` | woStrategy | `REUSE_AS_IS` | Pass only supported public settings. |
| Empirical pit loss | `woStrategy/src/wostrategy/script/race_performance_review.py`: `pit_loss_split_summary()` | woStrategy | `EXTRACT_PUBLIC_API` | Re-export from an analysis module so applications do not import script code. Existing output contains median and mean split/total. |
| Planner pit-loss split consumer | `woPlanner/src/woplanner/standard/strategy_model.py`: `WoStrategyRaceModelAdapter.pit_loss_split()` | woPlanner | `REUSE_AS_IS` | Confirmed it reads `PitInS3LossMedianSeconds` and `PitOutS1LossMedianSeconds`. |
| SC/VSC and mixed pit state | `pit_loss_split_summary()` uses `_combine_pit_status()`; `woPlanner/src/woplanner/standard/service.py`: `track_status_type()`, `estimate_actual_pit_loss_by_lap()` | woStrategy empirical summary, woPlanner counterfactual service | `EXTRACT_PUBLIC_API` | Existing empirical summary excludes cross-state samples from pure-state groups by labelling them `mixed`. Pure actual-event counterfactual calculation still needs extraction from woPlanner to woStrategy before automated Event-Aware Hindsight Optimum can be production-enabled. |
| SC/VSC-aware hindsight planning | `woPlanner/src/woplanner/standard/service.py`: scenario comparison, `estimate_actual_pit_loss_by_lap()`, `apply_actual_pit_loss_distribution()` | woPlanner | `EXTRACT_PUBLIC_API` | Large GUI/service-coupled calculation; expose as a woStrategy deterministic API before `woWeekend-post` enables Event-Aware Hindsight Optimum. Do not simulate GUI actions. |
| Standings and projection | `woStanding/src/wostanding/analysis/points_progression.py`: `calculate_points_progression()`; `plots/points_progression.py`; script API `run_points_progression()` | woStanding | `REUSE_AS_IS` | Post workflow adapter calls the public script/API and retains driver/team actual and projected tables/figures. Projection method is unchanged. |
| woData cache/path ownership | `woData/src/wodata/__init__.py`: `get_data_root()`, canonical cache helpers; `artifacts.py`: atomic-ish/versioned model/search artifacts | woData | `EXTEND_EXISTING` | Add generic `WeekendRunStore` with atomic JSON/byte writes, immutable run artifacts, latest references, historical/pre-start selection, and semantic external references. woData remains unaware of business fields. |
| Existing report prompts/schema | No reusable report-bundle implementation found in the four repositories | none | `NEW_IMPLEMENTATION_REQUIRED` | Implement presentation-only bundle assembly in woWeekend, capped at 20 files and excluding telemetry. |

## Implementation boundaries and known gaps

- V1 infrastructure can support partial component status. A missing public analytical
  adapter is recorded as unavailable/failed; the workflow does not invent a method.
- Event-Aware Hindsight Optimum remains explicitly unavailable until the planner-owned deterministic
  counterfactual is extracted into woStrategy. Surrounding post-run selection,
  fallback provenance, comparison, manifests and reports can still operate.
- Producer freshness metadata varies. The provider-neutral tyre API can validate its
  versioned cache metadata; source-session-aware freshness remains the producing
  model's responsibility and is reported when unavailable.
