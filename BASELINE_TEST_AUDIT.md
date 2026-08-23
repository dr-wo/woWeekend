# Baseline regression audit

The comparison used clean `git archive HEAD` snapshots for each existing repository
and isolated temporary `WODATA_ROOT` directories. The woWeekend package is new and
therefore has no prior baseline.

| Test group | Baseline | Current | Classification | Notes |
|---|---:|---:|---|---|
| woStrategy full suite | 216 passed, 4 failed | 216 passed, 4 failed | `PRE_EXISTING` | The same four tests fail in both snapshots: three `quali_performance` evolution/team-best tests and `relative_team_pace_rows_use_sample_paired_relative_baseline_when_available`. |
| woPlanner full suite, isolated data root | 149 passed, 20 failed | 153 passed, 17 failed | `PRE_EXISTING` | Every current failure is also present at baseline. Current code fixes three baseline live-service failures. |
| woPlanner shared/default data-root run | n/a | one additional failure | `ENVIRONMENT_OR_SHARED_DATA_DEPENDENT` | `test_service_defaults_pit_loss_from_first_lap_after_stop_status` passes with an isolated data root; shared cached state changes its input selection. |
| woData focused contracts/events/run-store | n/a | passed | `REGRESSION_FROM_THIS_WORK`: none | New and modified contracts pass. |
| woWeekend suite | n/a | passed | `REGRESSION_FROM_THIS_WORK`: none | New package tests cover config, orchestration, transition snapshots, and report bundles. |

Current woPlanner failures inherited from baseline are the reference-delta and
simulation-service tests, track-status classification, weighted-median cache adapter,
SC/VSC residual handling, observed-pit removal, overlays, and modelled comparisons.
No failure unique to this implementation remains.

## Individual current failures

| Test | Baseline | Current | Classification |
|---|---|---|---|
| `test_calculate_quali_performance_reports_two_driver_team_bests` | fail | fail | `PRE_EXISTING` |
| `test_calculate_quali_performance_uses_top_drivers_for_evolution_fit` | fail | fail | `PRE_EXISTING` |
| `test_calculate_quali_performance_can_use_exponential_evolution_fit` | fail | fail | `PRE_EXISTING` |
| `test_relative_team_pace_rows_use_sample_paired_relative_baseline_when_available` | fail | fail | `PRE_EXISTING` |
| `test_reference_delta_calculation_uses_selected_driver` | fail | fail | `PRE_EXISTING` |
| `test_service_pit_window_uses_current_reference_delta` | fail | fail | `PRE_EXISTING` |
| `test_track_status_event_data_classifies_sc_and_vsc_laps` | fail | fail | `PRE_EXISTING` |
| `test_strategy_model_adapter_reads_weighted_median_cache` | fail | fail | `PRE_EXISTING` |
| `test_service_creates_simulation_without_mutating_base_session` | fail | fail | `PRE_EXISTING` |
| `test_service_replaces_existing_driver_simulation` | fail | fail | `PRE_EXISTING` |
| `test_service_adds_multiple_edits_to_one_driver_scenario_and_rebuilds_from_earliest` | fail | fail | `PRE_EXISTING` |
| `test_service_remove_strategy_edit_rebuilds_then_deletes_empty_scenario` | fail | fail | `PRE_EXISTING` |
| `test_service_observed_comparison_uses_actual_future_lap_times` | fail | fail | `PRE_EXISTING` |
| `test_pure_sim_suppresses_sc_vsc_observed_residual` | fail | fail | `PRE_EXISTING` |
| `test_simulated_overlay_uses_strategy_delta_on_existing_cumulative_delta_plot` | fail | fail | `PRE_EXISTING` |
| `test_modelled_real_display_overlay_keeps_future_observed_pit_stop_unless_removed` | fail | fail | `PRE_EXISTING` |
| `test_modelled_real_display_overlay_removes_split_future_observed_pit_loss` | fail | fail | `PRE_EXISTING` |
| `test_remove_observed_pit_edit_creates_no_pit_strategy_from_actual_marker_lap` | fail | fail | `PRE_EXISTING` |
| `test_simulated_reference_recalculates_all_driver_deltas` | fail | fail | `PRE_EXISTING` |
| `test_simulated_driver_delta_is_against_selected_reference_not_own_observed_delta` | fail | fail | `PRE_EXISTING` |
| `test_service_modelled_comparison_predicts_actual_remaining_strategy` | fail | fail | `PRE_EXISTING` |
| `test_service_defaults_pit_loss_from_first_lap_after_stop_status` (shared data root only) | not reproduced in isolation | fail with shared cache; pass in isolation | `ENVIRONMENT_OR_SHARED_DATA_DEPENDENT` |
