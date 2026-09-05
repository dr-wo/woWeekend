# Live-MC model-config prerequisite

## Why this exists

woPlanner's live tyre Monte Carlo requires the event-specific woStrategy
`model_config.json` produced from the available free-practice sessions. Before
this integration, Race Preparation did not guarantee that artifact existed and
Post's historical live-MC replay failed with:

```text
Run pre_race_analysis first; model_config.json is missing.
```

That made an internal implementation prerequisite part of the operator
workflow. It also meant older races could not be replayed unless the operator
manually reconstructed the missing setup first.

## Production design

The shared owner-level API is:

```python
wostrategy.analysis.ensure_pre_race_model_config(
    season=2026,
    round_number=12,
    data_root=data_root,
    session_names=event_metadata.session_names,
)
```

It does not calculate a tyre model. It validates the event-local artifact and,
when generation is required, calls the existing canonical producer:

```text
wostrategy.script.pre_race_analysis.run_from_script_config
```

The calculation settings and numerical implementation therefore remain owned
by woStrategy. woWeekend only decides when the prerequisite must be ensured and
how its outcome affects workflow components.

The resulting flow is:

```text
Race Preparation
  resolve event and practice-session shape
  -> ensure event model_config.json
       -> reuse valid/fresh artifact
       -> otherwise run canonical pre_race_analysis producer
  -> save live_mc_model_config.json provenance in the immutable run

Post-Race
  load the selected pre-race prediction
  -> ensure the same event model_config.json
  -> reconstruct historical live-MC evolution
  -> retain final full-race Retro MC as a separate result
```

The replay API also ensures the prerequisite when called directly. The Post
workflow passes its already-ensured result into replay so normal execution does
not validate or generate twice.

## Validation and freshness policy

An existing config is reused when all of the following hold:

- it is found under the exact `year=<year>/round=<round>` woData artifact path;
- required live-model fields exist and have valid types/ranges;
- embedded season/round identity, when present, matches the requested event;
- embedded source practice sessions, when present, match event metadata;
- it is not explicitly marked stale;
- no selected free-practice lap cache is newer than the config file.

Newly generated artifacts include schema version, event identity, source
sessions, generation timestamp, and producer API. Standard weekends select
FP1/FP2/FP3; sprint weekends select only the free-practice sessions actually
listed by event metadata (normally FP1).

The API returns compact provenance with `status` equal to `reused` or
`generated`, the exact path, event identity, selected sessions, validation
reason, and canonical producer API. Component provenance is copied into
`manifest.json` under `output_provenance`.

Cross-round fallback is deliberately forbidden. Failure to build Round N never
causes the workflow to search for or silently load another round's config.

## Failure semantics

Race Preparation records `live_mc_model_config` as failed if automatic
generation fails, while retaining the diagnostic and continuing other safe
components.

Post treats generation as a prerequisite only for `live_mc_history`. A genuine
generation failure:

- marks `live_mc_history` failed;
- records `generation_failed`, the exception, and `failure_isolated: true`;
- does not invoke replay with a missing prerequisite;
- does not fail standings, performance trackers, the fresh full-race Retro MC,
  or unrelated Post artifacts.

This can make the overall Post run `PARTIAL`, which is intentional: useful and
trustworthy results remain available without pretending live history succeeded.

## Deliberate compromises

### Freshness uses source modification times

The check compares `model_config.json` with selected FP lap-cache modification
times instead of hashing every input and all producer code. This is fast and
works with existing woData conventions. It can conservatively regenerate after
a cache file is touched, and it cannot detect content changes that preserve an
old timestamp. Embedded identity/session metadata and required-field validation
cover the most dangerous errors, especially cross-event reuse.

### Legacy event-local configs remain reusable

Configs created before schema version 2 may not contain embedded event identity
or source sessions. They are accepted only from the exact event-scoped woData
path when their model fields are valid and their file is fresh relative to the
relevant FP caches. Rejecting all legacy artifacts would force expensive
regeneration for every historical race; accepting files from a global or
different-round path would risk using the wrong model. The event-scoped-path
requirement is the compatibility boundary between those concerns.

### Generation uses cache-first canonical defaults

Automatic generation sets the requested event and practice sessions, disables
the optional diagnostic plot, and keeps forced cache refresh off. This avoids an
unnecessary figure and prefers existing local FP data. If required FP data are
not cached, the canonical producer may still need network access and can fail;
that failure is explicit and isolated rather than replaced by another model.

### Event metadata determines sprint shape

The assurer derives FP1/FP2/FP3 from the resolved event session list. This
prevents a sprint weekend from being treated as if FP2 and FP3 existed. When no
metadata is supplied to the low-level API, conventional FP1/FP2/FP3 is the
compatibility default. When metadata is supplied but contains no recognized
free-practice session, generation fails instead of guessing.

### Automatic generation may be expensive

The canonical pre-race Monte Carlo is intentionally reused without a reduced
sample count or alternate approximation. A missing historical prerequisite can
therefore add meaningful runtime to Post. This preserves numerical equivalence
with woPlanner live mode and avoids introducing a second, faster-but-different
MC implementation in woWeekend.

## Validation evidence

Focused tests cover missing-config generation, valid-config reuse, rejection of
wrong-round identity, Race Preparation integration, historical Post self-heal,
replay continuation, manifest provenance, and isolated generation failure.

The 2026 Round 12 Post smoke test ran without a manual pre-race CLI invocation.
Its existing fresh FP1 config was reused, a complete replay was reconstructed
from four local fragments, and 54 live-MC updates were produced. All updates
contained RMSE/ESS quality data, all three dry compounds evolved, and no replay
snapshot contained a lap beyond its leader-lap cutoff. Both live-MC figures were
included in a 13-file report bundle, below the 20-file limit.

This smoke result verifies the production path, but it is not a permanent
golden numerical fixture: source cache contents and canonical model settings may
legitimately change.
