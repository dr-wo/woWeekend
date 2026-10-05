# DBG-10 — Current-round standings availability

**DBG-10 root-cause diagnosis: CLOSED. Fix: implemented.**

During the original 2026 R16 post-race run, Jolpica returned no race results.
FastF1 still supplied a driver roster with missing `Points`. woStanding silently
converted those missing awards to zero, and woWeekend recorded `SUCCESS` with
no warnings. Publication latency is expected upstream behavior; treating an
unpublished race as a zero-point race was the defect. The recovered empty
upstream response and driver roster are preserved in
`woStanding/tests/fixtures/dbg10/empty_r16_upstream.json` and
`woStanding/tests/fixtures/dbg10/empty_r16_session_results.json`.

## Guard and degraded output

The woStanding loader now requires at least one usable driver/team row with a
finite numeric upstream `Points` value before a session enters the normal
zero-filling path. With the existing `session.load(laps=False, ...)` call, that
field comes from Jolpica's race/sprint result, not the F1 driver roster or a
timing-derived classification. This detects absent publication without checking
point sums, changes in totals, a particular driver, grid size, position, or DNF
classification. Numeric zero is valid; partially missing optional fields and
individual missing points retain the existing normalization behavior once a
usable upstream result is present.

A missing target race (or target event absent from the schedule) raises
`StandingsUnavailableError` before calculation/export/plotting. If sprint points
are included for the target sprint weekend, its sprint result must also be
usable; sprint results alone cannot substitute for race results. Existing
historical-round skipping remains unchanged.

The woWeekend standings adapter converts the exception into the existing
`ComponentResult` status `UNAVAILABLE`, with an explicit current-round warning,
empty actual/projected standings, target round, reason and missing session.
It persists that state in `standings.json`, `analysis_results.json`, manifest
outputs/warnings and report-context/bundle inputs. Other components continue;
the overall status is `PARTIAL` when other components succeed. The bundle omits
standings figures when unavailable, including existing files passed to a
report-only regeneration, and instructs the report to state the warning rather
than label previous-round totals as current-round standings.

## Verification

Focused regressions cover the recovered empty R16 response/driver roster,
published R16 awards and all verified driver/constructor totals, genuine zero
awards (including an all-zero published result), partial fields, missing target
schedule entries, historical R15, sprint race/sprint availability, component
isolation, artifact/warning serialization and report-only regeneration.
Fixtures in `woStanding/tests/fixtures/dbg10` retain the recovered upstream
response and the later response; prior event points include verified sprint
awards. FastF1 driver/session fixtures preserve F1 roster team names, which can
differ from Jolpica constructor names.

An additional offline FastF1 replay uses a cloned diagnostic cache, never the
production cache. The empty response emits `UNAVAILABLE` without plots/CSVs;
the published R16 output matches the entire previously verified standings JSON.
Historical R15 and the Chinese R2 sprint weekend preserve verified actual
standings. The committed `woStanding/tests/fixtures/dbg10` evidence includes
the published R16 upstream response, FastF1 result rows, verified R16 totals,
and prior-round event points used by the regression tests.

Validation completed with **58 passing tests** across woStanding and the
post-race, bundle, BSF and manifest suites. One additional manifest fingerprint
test fails with `traffic-unavailable` instead of `updated`; the same failure was
reproduced with the preserved pre-change source. The independent read-only
review found no substantive DBG-10 issues. Both repositories pass
`git diff --check`; hashes confirm all 37 unrelated pre-existing modified files
remain unchanged.

No polling, automatic retry/publication monitoring, HTTP cache-policy change,
provider change, standings formula change or unrelated performance/MC change
was added. Existing partial-row normalization is not a certification of every
upstream driver field or an audit of historical input completeness.
