from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

import pandas as pd

from woweekend.artifacts.models import ComponentResult
from woweekend.config.common import resolved_dict
from woweekend.config.qualifying import QualifyingConfig
from woweekend.reports.bundle import create_report_bundle
from .common import create_run, finish_run, resolve_workflow_event


def effective_track_value(calculated_value: float, *, manual_active: bool, manual_value: float | None) -> dict[str, Any]:
    if manual_active and manual_value is None:
        raise ValueError("manual_value is required while manual override is active")
    return {"calculated_value": float(calculated_value), "effective_value": float(manual_value if manual_active else calculated_value), "manual_override": {"active": bool(manual_active), "value": None if manual_value is None else float(manual_value)}}


class QualifyingSnapshotRecorder:
    def __init__(self, run) -> None:
        self.run = run
        self.saved: set[str] = set()

    def save_transition(self, completed_part: str, snapshot: Any) -> None:
        part = completed_part.upper()
        if part not in {"Q1", "Q2", "Q3", "SQ1", "SQ2", "SQ3"}:
            raise ValueError("Snapshots are only saved at Q/SQ session transitions")
        if part in self.saved:
            return
        value = asdict(snapshot) if hasattr(snapshot, "__dataclass_fields__") else snapshot
        self.run.save_json(f"snapshot.{part}", value)
        self.saved.add(part)


def historical_qualifying_transitions(
    laps: Any,
    *,
    session: str,
    push_threshold_fraction: float,
) -> list[tuple[str, dict[str, Any]]]:
    """Calculate Q/SQ transition state with the shared qualifying-only estimator."""
    from wostrategy.analysis.live_qualifying import (
        calculate_live_qualifying_track_evolution,
    )

    prefix = "SQ" if session.upper() == "SQ" else "Q"
    split = laps.pick_accurate().split_qualifying_sessions()
    transitions: list[tuple[str, dict[str, Any]]] = []
    for index, part_laps in enumerate(split, start=1):
        part = f"{prefix}{index}"
        if part_laps is None or part_laps.empty:
            transitions.append((part, {
                "status": "UNAVAILABLE", "reason": "No accurate laps in session part",
                "source_scope": "QUALIFYING_ONLY", "evidence_count": 0,
            }))
            continue
        seconds = pd.to_timedelta(part_laps["LapTime"], errors="coerce").dt.total_seconds().dropna()
        if seconds.empty:
            transitions.append((part, {
                "status": "UNAVAILABLE", "reason": "No valid lap times in session part",
                "source_scope": "QUALIFYING_ONLY", "evidence_count": 0,
            }))
            continue
        fastest = float(seconds.min())
        push = seconds[seconds <= fastest * (1.0 + push_threshold_fraction)].tolist()
        try:
            estimate = calculate_live_qualifying_track_evolution(push)
        except ValueError as exc:
            transitions.append((part, {
                "status": "UNAVAILABLE", "reason": str(exc),
                "source_scope": "QUALIFYING_ONLY", "evidence_count": len(push),
                "reference_lap_s": fastest,
            }))
            continue
        transitions.append((part, {
            "status": "SUCCESS",
            "calculated_value": estimate.rate_s_per_field_push,
            "effective_value": estimate.rate_s_per_field_push,
            "manual_override": {"active": False, "value": None},
            "source_scope": estimate.source_scope,
            "evidence_count": estimate.evidence_count,
            "reference_lap_s": fastest,
            "push_threshold_fraction": push_threshold_fraction,
        }))
    return transitions


def _json_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    def scalar(value: Any) -> Any:
        if value is None or pd.isna(value):
            return None
        if isinstance(value, (pd.Timestamp, pd.Timedelta)):
            return value.isoformat()
        item = getattr(value, "item", None)
        return item() if callable(item) else value

    return [
        {str(key): scalar(value) for key, value in row.items()}
        for row in frame.to_dict("records")
    ]


def run_qualifying(config: QualifyingConfig, *, event: str, input_config: dict[str, Any] | None = None, analyzer: Callable[..., Any] | None = None, laps=None, qualifying_laps=None, event_resolver=None):
    metadata = resolve_workflow_event(
        event, config, include_race_distance=False,
        **({"resolver": event_resolver} if event_resolver is not None else {}),
    )
    resolved = resolved_dict(config)
    resolved.update({"event_id": metadata.event_id, "event_name": metadata.event_name, "season": metadata.season, "round_number": metadata.round_number, "event_metadata": metadata.to_dict()})
    run = create_run(event=metadata.event_id, workflow="qualifying_preparation", data_root=config.data_root, input_config=input_config or resolved_dict(config), resolved_config=resolved, event_metadata=metadata)
    components: dict[str, ComponentResult] = {}
    try:
        if analyzer is None:
            from wostrategy.analysis.pre_quali import analyze_pre_quali
            analyzer = analyze_pre_quali
        if laps is None:
            from woplanner.quali.analysis import QualiAnalysisService
            result = QualiAnalysisService(config.data_root).analyze(metadata.season, metadata.round_number, push_threshold_fraction=config.push_threshold_fraction)
        else:
            result = analyzer(laps, year=metadata.season, round_number=metadata.round_number, push_threshold_fraction=config.push_threshold_fraction)
        output = {
            "durations": _json_records(result.durations),
            "track_progression": _json_records(result.track_progression),
            "lap_roles": _json_records(result.laps),
        }
        run.save_json("qualifying_preparation", output)
        components["pre_session_analysis"] = ComponentResult("SUCCESS", output, provenance={"owner": "woStrategy", "api": "analyze_pre_quali"})
    except Exception as exc:
        components["pre_session_analysis"] = ComponentResult("FAILED", error=f"{type(exc).__name__}: {exc}")
    try:
        if qualifying_laps is None:
            from wostrategy.core.session import Session
            qualifying_laps = Session(metadata.season, metadata.round_number, config.session).laps
        recorder = QualifyingSnapshotRecorder(run)
        transitions = historical_qualifying_transitions(
            qualifying_laps,
            session=config.session,
            push_threshold_fraction=config.push_threshold_fraction,
        )
        for part, snapshot in transitions:
            recorder.save_transition(part, snapshot)
        track_output = {part: snapshot for part, snapshot in transitions}
        run.save_json("qualifying_track_evolution", track_output)
        successful = sum(item["status"] == "SUCCESS" for item in track_output.values())
        status = "SUCCESS" if successful == len(track_output) else ("PARTIAL" if successful else "FAILED")
        components["qualifying_track_evolution"] = ComponentResult(
            status,
            track_output,
            provenance={"owner": "woStrategy", "api": "calculate_live_qualifying_track_evolution"},
        )
        components["session_transition_state"] = ComponentResult(
            "SUCCESS", {"saved_parts": [part for part, _ in transitions]},
            provenance={"policy": "one immutable snapshot per completed Q/SQ part"},
        )
    except Exception as exc:
        components["qualifying_track_evolution"] = ComponentResult("FAILED", error=f"{type(exc).__name__}: {exc}")
        components["session_transition_state"] = ComponentResult("FAILED", error=f"{type(exc).__name__}: {exc}")
    manifest = finish_run(run, components)
    run.save_json("report_context", {"warnings": manifest["warnings"], "presentation_language": config.report.language})
    create_report_bundle(run_path=run.path, workflow=run.workflow, event=metadata.event_id, context={"warnings": manifest["warnings"]}, results={name: item.to_dict() for name, item in components.items()}, language=config.report.language, template=config.report.template)
    return run
