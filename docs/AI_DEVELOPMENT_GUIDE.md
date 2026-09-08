# woWeekend development guide for humans and AI agents

This document describes the current repository as an orchestration system, not
just as a collection of functions. It is intended to be the first reference for
future AI-assisted development. Read `../AUDIT.md` as well when changing a
cross-repository capability: the audit records where each numerical method is
owned and whether `woWeekend` may call, extend, or must first extract an API.

The description reflects the repository state on 2026-08-29.

## Purpose and non-goals

`woWeekend` turns the existing `dr-wo` analysis packages into three repeatable,
independently invokable weekend workflows:

1. Qualifying Preparation (`woweekend-quali`)
2. Race Preparation (`woweekend-race`)
3. Post-Race (`woweekend-post`)

Its job is orchestration: resolve an event, validate configuration, invoke the
canonical owner of each calculation, persist small deterministic results, and
assemble a presentation bundle. It must not become a second implementation of
qualifying correction, tyre Monte Carlo, strategy optimisation, standings, or
race-performance calculations.

The ownership boundary is deliberate:

| Package | Primary responsibility used by woWeekend |
| --- | --- |
| `woData` | Event identity, cache roots, immutable weekend run storage, live recording/replay data |
| `woStrategy` | Qualifying/race performance, tyre models, degradation cutoffs, exact strategy search adapters |
| `woPlanner` | Existing operational/live analysis services and live-MC recalculation |
| `woStanding` | Driver/constructor progression and projection |
| `woWeekend` | Configuration, ordering, failure propagation, artifact selection, report bundling |

When adding an output, search those packages first. A thin adapter in the owner
package is preferable to copying calculation logic into `woWeekend`.

## Repository map

```text
woWeekend/
├── configs/                    checked-in examples; local run configs may be untracked
├── docs/                       durable development and architecture guidance
├── src/woweekend/
│   ├── artifacts/              component status and run-manifest helpers
│   ├── cli/                    three small command entry points and report-only mode
│   ├── config/                 strict dataclass parsers and self-documenting templates
│   ├── reports/                deterministic, capped report-bundle assembly
│   └── workflows/              qualifying, race, Post, and live replay orchestration
├── tests/                      unit and workflow-level contract tests
├── AUDIT.md                    cross-package capability ownership/reuse audit
└── README.md                   operator-facing usage summary
```

Important entry points:

- `workflows/common.py`: event resolution, immutable run creation, component
  status collection, and manifest finalisation.
- `workflows/qualifying.py`: FP/pre-qualifying analysis plus immutable Q/SQ
  transition snapshots.
- `workflows/race.py`: pre-race tyre selection, manual overlay, exact strategy
  searches, and degradation cutoff products.
- `workflows/post_race.py`: fresh Retro MC, current-event summaries,
  season-to-date trackers, comparison/strategy dependencies, standings, live
  replay, and report assembly.
- `workflows/live_mc_replay.py`: causally reconstructs historical live-MC
  updates by calling `woPlanner`; it does not implement another MC model.
- `reports/bundle.py`: copies deterministic JSON context plus selected figures
  into a bundle with at most 20 files.

## Common execution model

All three workflows follow the same lifecycle:

1. Resolve `YYYY-RR` through `wodata.events.resolve_event`.
2. Reject legacy season/round configuration that conflicts with the event.
3. Write input, resolved configuration, and event metadata into a new immutable
   `WeekendRunStore` run.
4. Execute independent components and record `SUCCESS`, `PARTIAL`, `FAILED`, or
   `UNAVAILABLE` with warnings and provenance.
5. Persist JSON only for successful deterministic outputs.
6. Write `analysis_results.json` and `manifest.json`.
7. Assemble a presentation-only report bundle.

Runs are stored below:

```text
<WODATA_ROOT>/woweekend/runs/schema_v1/
  event=<YYYY-RR>/workflow=<workflow>/runs/<run_id>/
```

Never mutate an earlier run to “repair” it. Start a new run and retain the old
manifest as evidence. `latest` is a convenience pointer, not provenance.

## Configuration principles

Configuration parsers reject unknown keys. This catches misspellings and stale
options early, but it means adding a setting requires coordinated changes to:

- the relevant dataclass and parser;
- its `template()` output;
- the checked-in example JSON;
- tests for valid, invalid, and default behavior;
- this guide when the setting changes workflow semantics.

Event identity comes from `--event`. Configuration `season` and `round_number`
remain compatibility fields and may only agree with the resolved event.

Post `qualifying_performance.overrides` and `race_performance.overrides` are
passed to the existing owner APIs. Orchestration-controlled identity and
freshness fields (`year`, race range, output root, session, and whether the
current Retro MC may use a cached result) are not user overrides. Plot-only race
settings are separated before calling the numerical producer.

## Qualifying Preparation

`run_qualifying` has two related but distinct outputs:

- FP/pre-session analysis delegates to `wostrategy.analysis.pre_quali` (or the
  planner service when loading its own data).
- Formal Q/SQ transition state delegates to
  `wostrategy.analysis.live_qualifying.calculate_live_qualifying_track_evolution`.

Assumptions:

- live qualifying track evolution uses qualifying-only evidence;
- accurate lap-time observations within the configured fraction of the fastest
  reference are treated as push laps;
- each completed Q/SQ part is saved once and never overwritten;
- a missing part is represented explicitly as unavailable rather than inferred
  from FP data.

Compromise: historical execution reconstructs transition snapshots after the
fact. The persisted state has the same calculation scope, but it is not a claim
that the offline orchestration reproduced every operational UI interaction.

## Race Preparation

`run_race` resolves race distance, pit loss, tyre inputs, exact strategies, and
degradation cutoff envelopes.

Race Preparation also calls
`wostrategy.analysis.ensure_pre_race_model_config` before strategy products are
calculated. A valid/fresh event config is reused; otherwise the shared API runs
the canonical `pre_race_analysis` producer. The immutable Race run stores
`live_mc_model_config.json` so woPlanner readiness and producer provenance are
auditable.

The tyre flow is:

```text
canonical pre-race tyre artifact
       + optional per-value manual overlays
       ↓
effective MEDIUM-reference tyre model
       ↓
unrestricted and rules-compliant exact searches
       ↓
unrestricted and rules-compliant degradation cutoffs
```

Assumptions and compromises:

- MEDIUM remains the performance reference and its effective delta must be zero.
- Automatic uncertainty is retained in artifacts, but the strategy optimiser
  consumes central values; it is not an uncertainty-aware optimiser.
- If the automatic tyre artifact is absent, a manual fallback is accepted only
  when all three dry compounds have complete performance and degradation values.
- Pit loss accepts either one total or an S3/S1 pair. Mixing those forms is
  rejected because precedence would otherwise be ambiguous.
- The rules-compliant mode currently represents the dry-compound diversity rule;
  it is not a complete FIA sporting-rules engine.
- The adaptive cutoff scan may extend an automatic lower bound toward zero to
  find a positive 1→2 crossing. A user-supplied lower bound is treated as hard.

## Post-Race dependency graph

Post deliberately distinguishes current-event review, season trackers, live
history, and the final full-race Retro estimate.

```text
canonical fresh full-race Retro MC
          ├── current race-performance review
          ├── empirical pit-loss summary
          ├── pre-race vs Retro tyre comparison
          └── Retro Green Optimum

R01..current qualifying reviews ──> qualifying season tracker + figures
R01..current race reviews ────────> race season tracker + figure

saved pre-race tyre + causal replay cuts
          └── historical live-MC evolution (separate from full-race Retro)
```

### Fresh Retro MC guarantee

`run_post_race` calls
`wostrategy.analysis.post_race_review.get_post_race_review(force_refresh=True)`.
The adapter invokes the canonical
`wostrategy.script.race_performance_review.run_race_performance_review` with
`use_cached_monte_carlo=False` for the current round. That producer in turn owns
the call to `calculate_monte_carlo_race_performance_review`.

Freshness is verified, not inferred from the presence of files. The returned
producer result must list the current round in `race_results`; otherwise Post
fails Retro even if stale files still exist. Normalized output records:

- `producer_called`;
- `monte_carlo_executed`;
- `executed_rounds`;
- effective cache-reuse mode;
- tyre performance/degradation estimates and intervals;
- sample support, identifiability, RMSE/ESS quality, source files, and metadata.

The comparison and Green Optimum are gated on a validated non-empty Retro
result. Both persist the same Retro provenance. If Retro fails, these dependent
components fail rather than returning defaults.

Compromise: forcing the current round is expensive, but it is necessary to make
a Post run an auditable execution rather than a wrapper around unknown cached
state. The subsequent R01-to-current race tracker is allowed to reuse canonical
event caches, including the fresh current result, so Post does not rerun every
historical MC.

### Season-to-date performance trackers

The qualifying tracker reuses
`plot_quali_performance_range(race_start=1, race_end=current)` and its existing
analysis/plot functions. The race tracker reuses the stable race-review adapter
over `races=[1, ..., current]` and `plot_race_performance_results`.

Tracker JSON records requested, included, and unavailable rounds, the relative
team-performance rows, comparison team, method, and figure paths. This makes a
wet/unavailable round distinguishable from an accidentally shortened request.

Assumption: “season to date” means championship rounds 1 through the canonical
current round, inclusive. It does not infer non-championship or testing events.

Compromise: qualifying range analysis may reload session data and can be slow in
offline environments because FastF1 tries network endpoints before using local
cache. This affects latency/log verbosity, not the calculation method.

### Historical live-MC replay

`live_mc_replay.reconstruct_live_mc_history` builds leader-lap clock cuts and
passes each eligible snapshot to
`woplanner.analysis.service.OfflineAnalysisService.recalculate_from_live_snapshot`.
The full-race Retro output is shown as a separate endpoint and is never used as
historical lap input.

History coordinates must pass through `OfflineAnalysisService.strategy_assumptions`,
the shared live resolver. Do not replace an unobserved compound with its prior:
it may have a `live_derived` estimate. Preserve raw `calculated_value` separately
from resolved `algorithm_value` and override-aware `effective_value`. Plot the
latter using `effective_source`, not the direct-evidence `informed` flag.
The [R13 regression notes](LIVE_MC_DEGRADATION_HISTORY.md) document this boundary.

Before replay, Post calls the same shared woStrategy prerequisite API used by
Race Preparation. Missing or stale historical configs are generated
automatically through the canonical `pre_race_analysis` implementation. Replay
receives the ensured artifact provenance and does not implement or substitute
any model-config calculation. Direct low-level replay calls also self-ensure.

Replay modes:

- `algorithm_only`: report the live algorithm coordinates without reconstructing
  manual operator overrides.
- `operational`: retain calculated coordinates and separately replay recorded
  manual/effective values when override messages exist.

Assumptions:

- completed leader laps define the causal update clock;
- scheduled total laps are event metadata, not future observed lap evidence;
- a replay is complete only when its leader/tyre coverage reaches race distance;
- fragmented local recordings may be merged only after event/session identity
  validation.

Compromises:

- if local fragments are incomplete, the workflow may use/download the canonical
  archive replay;
- legacy model configs without embedded identity remain reusable only from the
  exact event-scoped path and only when structurally valid and fresh relative to
  selected FP caches;
- freshness uses FP cache modification times rather than full content/code
  hashing, balancing inexpensive normal startup against conservative rebuilds;
- automatic generation uses the canonical sample count and can be expensive or
  require uncached FP downloads; numerical equivalence is preferred over a
  second reduced-cost model;
- a replay failure is isolated so the full-race Retro and season trackers can
  still complete;
- operational mode can only reproduce manual actions that were actually recorded.

See
[`LIVE_MC_MODEL_CONFIG_PREREQUISITE.md`](LIVE_MC_MODEL_CONFIG_PREREQUISITE.md)
for the complete validation policy, provenance contract, and design rationale.

## Report bundles

The report bundle contains instructions, context, deterministic analysis JSON,
a figure manifest, and selected images. Numerical JSON is authoritative; images
are presentation aids. Raw telemetry and large reusable MC tables remain outside
the bundle.

The hard limit is 20 files. Five structural files are reserved, leaving at most
15 images. Image ordering is deterministic, with live-MC evolution figures
prioritized before alphabetical fallback. Season tracker figures and normalized
Retro results are available through the same `analysis_results.json` bundle.

Do not add arbitrary CSV/telemetry copying to the report builder. Add a compact
deterministic JSON summary or an intentional figure instead.

## Failure semantics

- `FAILED` means a requested component could not produce a trustworthy result.
- `UNAVAILABLE` means the capability is knowingly not exposed; it is not silently
  replaced.
- `PARTIAL` at run level is expected when useful outputs exist alongside a failed
  or unavailable component.
- Downstream components must test prerequisite status and data shape explicitly.
- Never mark a dependent component successful using `{}`, defaults, or an adapter
  object that was not executed.

The Event-Aware Hindsight Optimum remains `UNAVAILABLE`. Its deterministic
SC/VSC counterfactual is still coupled to planner service/UI behavior and must be
extracted into an owner-level API before Post enables it. Retro Green Optimum is
not a substitute: it is a hypothetical all-green optimisation.

## Testing and validation

Run `woWeekend` tests from its repository with all sibling sources visible:

```bash
PYTHONPATH=../woData/src:../woStrategy/src:../woStanding/src:../woPlanner/src:src \
MPLBACKEND=Agg MPLCONFIGDIR=/tmp/mpl-woweekend \
../.venv/bin/python -m pytest -q
```

Use a headless Matplotlib backend. On macOS the default GUI backend can abort a
test process even when the calculation is correct.

For a Post change, tests should cover at least:

- Post overrides reach both current-event and season-range owner APIs;
- requested tracker range is exactly `1..current`;
- fresh Retro execution is recorded and numerical output is non-empty;
- missing/empty Retro blocks comparison and Green Optimum;
- both dependents retain the same Retro provenance;
- figures referenced by JSON exist in the run;
- report bundle file count is no greater than 20;
- live replay does not leak future laps into historical snapshots.
- missing and stale live-MC model configs invoke the canonical producer;
- valid event configs are reused and wrong-round identity is rejected;
- model-config generation failure is isolated from unrelated Post outputs.

The 2026-R11 integration smoke used during the tracker/Retro audit produced all
11 rounds in both trackers, a fresh 80,000-sample Retro run, and an 11-file
report bundle. Treat that as evidence for the audited code path, not a permanent
golden numerical fixture: source data and explicit analytical overrides may
legitimately change future values.

## Change checklist for future agents

Before editing:

1. Read `AUDIT.md` and locate the canonical owner API.
2. Inspect every affected repository's working tree; sibling repositories often
   contain unrelated uncommitted research work.
3. Decide which values are calculation inputs, orchestration guarantees, manual
   overrides, and presentation-only settings.
4. Identify prerequisite/failure edges before adding success artifacts.

While editing:

1. Keep calculations in their owner package.
2. Preserve source provenance and execution/freshness evidence.
3. Add strict config parsing and template help together.
4. Keep run artifacts small, deterministic, and JSON-serializable.
5. Preserve immutable historical runs and avoid implicit `latest` selection when
   an exact prior artifact is required.

Before committing:

1. Run focused owner-package tests and the full `woWeekend` suite.
2. Inspect the generated report bundle and count files.
3. Verify failure propagation with a deliberately failed prerequisite.
4. Stage only coherent files; do not absorb unrelated sibling-repository work.
5. Record any remaining unavailable capability or data/cache limitation.
