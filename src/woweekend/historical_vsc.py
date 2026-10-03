"""Evidence-only historical VSC lookup for race preparation."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from wodata.weekend_runs import WeekendRunStore, weekend_runs_root


ESTIMATOR_SEMANTICS = "GREEN-relative pit-sector excess (evidence only)"
ALLOWED_FALLBACK_STATUSES = {
    "no_event",
    "no_vsc",
    "vsc_no_qualified_pair",
    "not_acquired",
    "load_failed",
    "parse_failed",
    "known_layout_mismatch",
    "unknown_layout",
    "available_green_relative_excess",
}


def query_historical_vsc_evidence(
    *,
    metadata,
    data_root: str | Path | None,
    lookback_years: int = 5,
    missing_loader: Callable[[int, Mapping[str, object]], Mapping[str, object] | None]
    | None = None,
) -> dict[str, Any]:
    """Return per-year evidence without creating an operational candidate.

    Existing post-race artifacts are checked first.  A caller may inject a
    cache-aware single-race loader for missing years. The race CLI supplies the
    minimal FastF1 loader; library callers may leave it unset for an explicitly
    offline artifact-only query.
    """
    count = int(lookback_years)
    if count < 1:
        raise ValueError("historical VSC lookback must be at least one year")
    identity = _identity(metadata)
    years = []
    for year in range(int(metadata.season) - count, int(metadata.season)):
        result = _artifact_result(
            season=year,
            identity=identity,
            data_root=data_root,
        )
        artifact_context = None
        if result is not None and result.pop("_needs_fallback", False):
            artifact_context = result
            result = None
        if result is None:
            result = _fallback_result(
                season=year,
                identity=identity,
                loader=missing_loader,
            )
            if artifact_context is not None:
                result["artifact_context"] = artifact_context
        years.append(_evidence_only(year, result))
    return {
        "status": "evidence_only",
        "target_event": str(metadata.event_id),
        "target_identity": identity,
        "lookback_years": count,
        "year_range": [int(metadata.season) - count, int(metadata.season) - 1],
        "years": years,
        "aggregation": None,
        "operational_input": False,
        "report_context_only": True,
        "candidate_available": False,
        "apply_available": False,
        "estimator_semantics": ESTIMATOR_SEMANTICS,
    }


def load_single_race_vsc_evidence(
    season: int,
    identity: Mapping[str, object],
    *,
    schedule_loader=None,
    race_laps_loader=None,
    estimator=None,
) -> dict[str, Any]:
    """Minimally load one identity-matched Race and classify its VSC evidence.

    This is the explicit fallback used by the race CLI after artifact/cache
    lookup misses.  It loads laps and track status only: telemetry, weather and
    race-control messages are disabled.  Injectable loaders keep the evidence
    path deterministic in tests and let callers choose an isolated FastF1 cache.
    """
    if schedule_loader is None:
        import fastf1

        schedule_loader = lambda year: fastf1.get_event_schedule(
            year, include_testing=False
        )
    if race_laps_loader is None:
        race_laps_loader = _minimal_fastf1_race_laps
    if estimator is None:
        from wostrategy.analysis.pit_loss import median_pit_loss_by_state

        estimator = median_pit_loss_by_state

    country, location = _text(identity.get("country")), _text(identity.get("location"))
    if not country or not location:
        return {
            "status": "parse_failed",
            "reason": "stable country/location identity is unavailable",
            "source": "single_race_loader",
        }
    schedule = schedule_loader(int(season))
    if not isinstance(schedule, pd.DataFrame):
        return {
            "status": "parse_failed",
            "reason": "event schedule is not a DataFrame",
            "source": "single_race_loader",
        }
    required = {"Country", "Location", "RoundNumber"}
    if not required.issubset(schedule.columns):
        return {
            "status": "parse_failed",
            "reason": f"event schedule lacks {sorted(required.difference(schedule.columns))}",
            "source": "single_race_loader",
        }
    matches = schedule.loc[
        schedule["Country"].map(_text).eq(country)
        & schedule["Location"].map(_text).eq(location)
    ]
    if matches.empty:
        return {
            "status": "no_event",
            "reason": "no stable country/location match in season schedule",
            "source": "event_schedule",
        }
    if len(matches) != 1:
        return {
            "status": "parse_failed",
            "reason": "stable country/location identity matched multiple events",
            "source": "event_schedule",
        }
    try:
        round_number = int(matches.iloc[0]["RoundNumber"])
    except (TypeError, ValueError) as exc:
        return {
            "status": "parse_failed",
            "reason": f"invalid matched RoundNumber: {exc}",
            "source": "event_schedule",
        }
    laps = race_laps_loader(int(season), round_number)
    if not isinstance(laps, pd.DataFrame) or "TrackStatus" not in laps.columns:
        return {
            "status": "parse_failed",
            "reason": "Race laps are unavailable or lack TrackStatus",
            "source": "single_race_loader",
            "source_event": f"{int(season):04d}-{round_number:02d}",
        }
    common = {
        "source": "single_race_loader",
        "source_detail": "FastF1 Race laps and TrackStatus",
        "source_event": f"{int(season):04d}-{round_number:02d}",
        "match": {
            "country": identity.get("country"),
            "location": identity.get("location"),
        },
        "layout_comparability": "unknown",
        "load_profile": {
            "telemetry": False,
            "weather": False,
            "messages": False,
        },
    }
    has_vsc = laps["TrackStatus"].fillna("").astype(str).str.contains(r"[67]").any()
    if not has_vsc:
        return {
            "status": "no_vsc",
            "sample_count": 0,
            "reason": "Race laps contain no VSC TrackStatus code",
            **common,
        }
    try:
        result = estimator(laps)
        vsc = result["vsc"]
    except Exception as exc:
        return {
            "status": "parse_failed",
            "reason": f"{type(exc).__name__}: {exc}",
            **common,
        }
    if not isinstance(vsc, Mapping) or vsc.get("status") != "available":
        return {
            "status": "vsc_no_qualified_pair",
            "sample_count": int(vsc.get("sample_count", 0) or 0)
            if isinstance(vsc, Mapping) else 0,
            "reason": "VSC occurred but the strict estimator accepted no pit pair",
            **common,
        }
    try:
        value = float(vsc["total"])
        sample_count = int(vsc.get("sample_count", 0))
        if not math.isfinite(value) or value < 0 or sample_count <= 0:
            raise ValueError(
                "available VSC evidence requires a finite non-negative total "
                "and positive sample_count"
            )
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "status": "parse_failed",
            "reason": f"{type(exc).__name__}: {exc}",
            **common,
        }
    return {
        "status": "available_green_relative_excess",
        "value_seconds": value,
        "sample_count": sample_count,
        "samples": _samples(vsc.get("samples")),
        **common,
    }


def _minimal_fastf1_race_laps(season: int, round_number: int) -> pd.DataFrame:
    import fastf1

    session = fastf1.get_session(int(season), int(round_number), "R")
    session.load(laps=True, telemetry=False, weather=False, messages=False)
    return session.laps


def _artifact_result(
    *, season: int, identity: Mapping[str, object], data_root: str | Path | None
) -> dict[str, Any] | None:
    root = weekend_runs_root(data_root)
    if not root.exists():
        return None
    deferred: list[dict[str, Any]] = []
    scan_errors: list[str] = []
    for event_directory in sorted(root.glob(f"event={season:04d}-*")):
        event_id = event_directory.name.removeprefix("event=")
        try:
            runs = WeekendRunStore.list_runs(
                event=event_id, workflow="post_race", data_root=data_root,
            )
        except Exception as exc:
            scan_errors.append(f"{event_id}: {type(exc).__name__}: {exc}")
            continue
        for run in reversed(runs):
            try:
                prior_identity = run.load_json("event.metadata")
            except (FileNotFoundError, TypeError, ValueError):
                continue
            if not _same_circuit(prior_identity, identity):
                continue
            mismatch = _known_layout_mismatch(prior_identity, identity)
            if mismatch:
                return {
                    "status": "known_layout_mismatch",
                    "source_event": event_id,
                    "source_run_id": run.run_id,
                    "layout_comparability": "incompatible",
                    "reason": mismatch,
                }
            try:
                empirical = run.load_json("empirical_pit_loss")
            except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
                deferred.append({
                    "status": "artifact_parse_failed",
                    "source_event": event_id,
                    "source_run_id": run.run_id,
                    "reason": f"{type(exc).__name__}: {exc}",
                })
                continue
            if not isinstance(empirical, Mapping):
                deferred.append({
                    "status": "artifact_parse_failed",
                    "source_event": event_id,
                    "source_run_id": run.run_id,
                    "reason": "empirical_pit_loss is not an object",
                })
                continue
            vsc = empirical.get("vsc")
            common = {
                "source": "post_race_artifact",
                "source_event": event_id,
                "source_run_id": run.run_id,
                "match": _match_summary(prior_identity, identity),
                "layout_comparability": "unknown",
            }
            if not isinstance(vsc, Mapping):
                if isinstance(empirical.get("sc_vsc"), Mapping):
                    deferred.append({
                        "status": "unsupported_legacy_combined",
                        "reason": "artifact contains combined SC/VSC evidence only",
                        **common,
                    })
                    continue
                deferred.append({
                    "status": "artifact_missing_independent_vsc",
                    "reason": "artifact has no independent VSC result",
                    **common,
                })
                continue
            if vsc.get("status") == "available":
                try:
                    value = float(vsc["total"])
                    sample_count = int(vsc.get("sample_count", 0))
                    if not math.isfinite(value) or value < 0 or sample_count <= 0:
                        raise ValueError(
                            "available VSC evidence requires a finite non-negative "
                            "total and positive sample_count"
                        )
                except (KeyError, TypeError, ValueError) as exc:
                    deferred.append({
                        "status": "artifact_parse_failed",
                        "reason": f"{type(exc).__name__}: {exc}",
                        **common,
                    })
                    continue
                return {
                    "status": "available_green_relative_excess",
                    "value_seconds": value,
                    "sample_count": sample_count,
                    "samples": _samples(vsc.get("samples")),
                    **common,
                }
            reason = str(vsc.get("reason") or "").casefold()
            status = (
                "no_vsc" if "no_vsc" in reason or "no vsc" in reason
                else "vsc_no_qualified_pair" if "qualif" in reason or "pit" in reason
                else None
            )
            try:
                sample_count = int(vsc.get("sample_count", 0) or 0)
            except (TypeError, ValueError) as exc:
                deferred.append({
                    "status": "artifact_parse_failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                    **common,
                })
                continue
            if status is None:
                deferred.append({
                    "status": "artifact_insufficient_evidence",
                    "sample_count": sample_count,
                    "reason": str(
                        vsc.get("reason") or "artifact does not classify absence"
                    ),
                    **common,
                })
                continue
            return {
                "status": status,
                "sample_count": sample_count,
                "reason": str(vsc.get("reason") or "artifact does not classify absence"),
                **common,
            }
    if deferred or scan_errors:
        return {
            "_needs_fallback": True,
            "status": "independent_vsc_unresolved",
            "artifacts": deferred,
            "scan_errors": scan_errors,
        }
    return None


def _fallback_result(
    *,
    season: int,
    identity: Mapping[str, object],
    loader: Callable[[int, Mapping[str, object]], Mapping[str, object] | None] | None,
) -> dict[str, Any]:
    if loader is None:
        return {
            "status": "not_acquired",
            "reason": "no matching artifact/cache was available; fallback loading was not requested",
        }
    try:
        value = loader(season, identity)
    except Exception as exc:
        return {"status": "load_failed", "reason": f"{type(exc).__name__}: {exc}"}
    if value is None:
        return {"status": "no_event", "reason": "no matching event in season"}
    result = dict(value)
    status = str(result.get("status") or "")
    if status not in ALLOWED_FALLBACK_STATUSES:
        return {
            "status": "parse_failed",
            "reason": f"fallback returned unsupported status {status!r}",
        }
    result.setdefault("source", "single_race_loader")
    result.setdefault("layout_comparability", "unknown")
    return result


def _evidence_only(year: int, value: Mapping[str, object]) -> dict[str, Any]:
    result = dict(value)
    result.setdefault("source", "none")
    result.setdefault("sample_count", None)
    result.setdefault("layout_comparability", "unknown")
    result.update({
        "year": int(year),
        "operational_input": False,
        "report_context_only": True,
        "candidate_available": False,
        "apply_available": False,
        "estimator_semantics": ESTIMATOR_SEMANTICS,
    })
    return result


def _identity(metadata) -> dict[str, object]:
    return {
        key: value for key, value in (
            ("event_name", getattr(metadata, "event_name", None)),
            ("official_event_name", getattr(metadata, "official_event_name", None)),
            ("country", getattr(metadata, "country", None)),
            ("location", getattr(metadata, "location", None)),
            ("circuit", getattr(metadata, "circuit", None)),
            ("circuit_id", getattr(metadata, "circuit_id", None)),
            ("layout_version", getattr(metadata, "layout_version", None)),
        ) if value not in (None, "")
    }


def _same_circuit(left: object, right: Mapping[str, object]) -> bool:
    if not isinstance(left, Mapping):
        return False
    left_id, right_id = _text(left.get("circuit_id")), _text(right.get("circuit_id"))
    if left_id and right_id:
        return left_id == right_id
    pairs = (
        _text(left.get("country")), _text(right.get("country")),
        _text(left.get("location")), _text(right.get("location")),
    )
    if all(pairs):
        return pairs[0] == pairs[1] and pairs[2] == pairs[3]
    left_circuit, right_circuit = _text(left.get("circuit")), _text(right.get("circuit"))
    return bool(left_circuit and right_circuit and left_circuit == right_circuit)


def _known_layout_mismatch(
    left: Mapping[str, object], right: Mapping[str, object]
) -> str | None:
    for key in ("layout_version", "layout_id"):
        old, new = _text(left.get(key)), _text(right.get(key))
        if old and new and old != new:
            return f"{key} differs: {left.get(key)!r} != {right.get(key)!r}"
    return None


def _match_summary(
    left: Mapping[str, object], right: Mapping[str, object]
) -> dict[str, object]:
    return {
        key: right[key]
        for key in ("circuit_id", "country", "location", "circuit")
        if key in right and _text(left.get(key)) == _text(right.get(key))
    }


def _text(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _samples(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


__all__ = [
    "ESTIMATOR_SEMANTICS",
    "load_single_race_vsc_evidence",
    "query_historical_vsc_evidence",
]
