# Historical VSC M2

Reconciled from aec322f onto woWeekend main a662ad8. Default lookback is exactly
Y-5 through Y-1 (five rows); historical_vsc.lookback_years remains configurable.
Usable independent post-race artifacts precede missing-data single-race loading.
Stable circuit identity, not round number, matches prior editions; known layout
mismatches are rejected and unknown comparability stays explicit. Missing, malformed,
legacy-combined or unclassified evidence may fall through with provenance retained.

Race CLI supplies the single-race fallback. Library callers default to artifact-only
lookup. Each fallback disables telemetry, weather and messages. Existing estimator
semantics remain GREEN-relative pit-sector excess. No averaged value, operational
candidate, Apply action, persistence/promotion into current inputs or live/M3 update
is added. Report rows preserve status, source, sample support and layout comparability.

Current strategy, independent Pit/SC/VSC values, interruption history, traffic
context and BSF report instructions are retained. The old private one-season helper
is retained for existing callers/tests but is no longer the production query path.

Minimum checks: historical-VSC unit cases; config defaults/validation; race workflow
five-year rows, unchanged strategy results and adopted VSC; interruption and traffic
report assembly regressions. Tests inject fallback data; they do not fetch seasons.

Validation: 79 tests passed in the isolated candidate; the one sibling-data-path
R14 test passed separately from the source checkout with candidate src first.
Use MPLBACKEND=Agg for workflow plotting. Initial default macOS plotting aborted
in the headless harness; Agg resolved it. Total: 80 passed.
