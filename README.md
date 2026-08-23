# woWeekend

`woWeekend` is the lightweight orchestration layer for three independently
invokable race-weekend stages. Numerical analysis remains in `woStrategy`, data
paths and immutable run storage remain in `woData`, standings remain in
`woStanding`, and GUI/manual controls remain in `woPlanner`.

```bash
woweekend-quali --event 2026-07 --config qualifying.json
woweekend-race --event 2026-07 --config race.json
woweekend-post --event 2026-07 --config post_race.json
```

Generate or validate self-documenting configuration:

```bash
woweekend-race --generate-config race.json
woweekend-race --validate-config race.json
```

Regenerate presentation artifacts without rerunning deterministic analysis:

```bash
woweekend-race --event 2026-07 --report-only RUN_ID --language en-GB
```

Pit loss can be supplied as one whole value; no S3/S1 split is required:

```json
{
  "pit_loss": {
    "green": {"total": 20.5},
    "sc_vsc": {"total": 11.3}
  }
}
```

For either state, use `total` or the `pit_in_s3` + `pit_out_s1` pair, never both.

## Degradation cutoff scan

Race Preparation keeps MEDIUM as the degradation scan variable, preserves the
predicted S:M:H degradation ratios, and holds compound performance deltas fixed.
It generates both unrestricted and rules-compliant fixed-stop envelopes.

With `degradation_cutoff.scan_min: null`, the normal prediction-centred scan is
run first. If it does not bracket a positive 1→2 cutoff, the lower bound is
progressively extended toward zero. The search stops when it finds a bracket or
reaches zero, and never evaluates negative degradation. A numeric `scan_min` is
a hard user boundary and disables extension below that value.

Artifacts record the predicted MEDIUM degradation, initial and effective lower
bounds, cutoff values, and a 1→2 search status. A search that reaches zero
without a positive crossing reports `no_positive_degradation_crossing`; it does
not describe the result merely as outside the initial range. Cutoff figures
label the prediction and each available 1→2 and 2→3 cutoff.

Assumptions and compatibility choices:

- the existing exact optimiser and crossing refinement define each cutoff;
- the automatic extension applies only to 1→2, while existing 2→3 scan logic
  and non-monotonic warnings are preserved;
- zero may be evaluated as the terminal boundary, but a crossing exactly at zero
  is not considered a positive-degradation cutoff;
- the tyre prediction model is consumed unchanged.

Current compromises and follow-up work are documented in
`woStrategy/doc/DEGRADATION_CUTOFF.md`. In particular, extension uses
deterministic bound halving, status values remain strings for artifact
compatibility, and any future 2→3 extension should be designed separately.

Runs are stored beneath
`<WODATA_ROOT>/woweekend/runs/schema_v1/event=<event>/workflow=<workflow>/runs/<run_id>`.
Each run keeps input/resolved configs, small deterministic outputs, a manifest,
and a ChatGPT-ready bundle of at most 20 files. Heavy reusable caches are stored
as exact references with provenance and are never silently replaced by `latest`.

See [AUDIT.md](AUDIT.md) for the audit-first reuse map and known extraction gap
for the Event-Aware Hindsight Optimum.
