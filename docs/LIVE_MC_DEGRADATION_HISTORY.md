# Live degradation history: R13 report fix

The R13 history writer bypassed woPlanner's live compound resolver. It selected
the raw MC median for observed compounds and the pre-race baseline for every
other compound. SOFT had no direct race evidence, so its reported value stayed
at 0.238814 while MEDIUM reached 0.350988 at lap 18. The plotting code then
labelled that baseline `SOFT (assumed)` and plotted `algorithm_value`, even in
operational mode.

## Coordinate and source contract

`_history_update` now calls `OfflineAnalysisService.strategy_assumptions` with
the fixed pre-race degradation and compound-delta coordinates. That existing
resolver selects direct evidence and derives other degradation values using
initial ratios to MEDIUM, preferring a direct MEDIUM anchor.

- `calculated_value` retains the raw MC median, or null when no row exists.
- `algorithm_value` contains the shared resolver's value.
- `source` records `base`, `live_derived`, or `live_direct`.
- `effective_value` applies recorded overrides in operational mode;
  `effective_source` then becomes `manual_override`.
- `anchor_compound` and `initial_ratio_to_medium` describe degradation derivation.
- `informed` continues to mean direct evidence. A derived value is useful without
  becoming direct evidence. Existing uncertainty remains the raw MC interval,
  not a newly calculated derived-value interval.

History also records a lap-zero `initial_state`. The degradation figure plots
effective values using dotted base, dashed derived, solid direct, and dash-dot
override lines. Masking other source states prevents lines from joining
disjoint periods of the same source. Older histories without source metadata
are labelled `unknown`; replotting alone cannot repair their substituted values.

## Ordering and validation

MC constrains sampled compound ordering when its degradation-order setting and
temperature condition enable it. The live resolver preserves fixed initial
ratios for derived compounds; it does not add a universal sorting or clamping
step. The R13 report lost the relationship by mixing direct estimates with
unchanged baselines. No MC inference or live derivation algorithm was changed.

The expected R13 transitions are:

| Compound | Before evidence | Lap 17 | Lap 18 onward |
| --- | --- | --- | --- |
| MEDIUM | base | live_direct | live_direct |
| HARD | base | live_derived | live_direct |
| SOFT | base | live_derived | live_derived |

`test_r13_live_sources_values_and_plot_survive_history_roundtrip` uses an absent
SOFT MC row, checks these transitions and S >= M >= H, round-trips JSON, and
checks plotted values, source styles, gaps, and operational overrides.

Validation from the workspace root:

```bash
MPLBACKEND=Agg .venv/bin/python -m pytest woWeekend/tests/test_live_mc_replay.py woWeekend/tests/test_post.py -q
```

All 11 tests passed. Applying the resolver to all 35 retained R13 updates also
preserved ordering. Corrected lap-18 S/M/H values were
0.374669 / 0.350988 / 0.219112. This check reused saved MC results without rerunning
inference. The committed `examples/2026-R13` files remain the original immutable
run snapshots and predate this report fix.
