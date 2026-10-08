# R16 archived event identity — separate debug item

Status: **OPEN / uninvestigated**, recorded 2026-10-08. Separate from **DBG-5: CLOSED**.

The 2026-16 archived race-preparation identity contains suspicious
`country=Bahrain`, `location=Kuala Lumpur` values. The latest archived five-year
historical VSC lookup returned `no_event` for 2021–2025, reporting no stable
country/location match in each season schedule.

Evidence: woData run
`woweekend/runs/schema_v1/event=2026-16/workflow=race_preparation/runs/20261003T225359.171082Z-f370e12cf1/report_context.json`,
under `historical_vsc_evidence.target_identity` and `historical_vsc_evidence.years`.

Future scope: establish whether that archived identity was intended and why the
lookup found no matching events. No cause is asserted and no investigation or fix
is included in this documentation task. This item does not change DBG-5's optional,
explicit operational VSC contract or authorize automatic historical propagation.
