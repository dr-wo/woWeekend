"""Causal reconstruction of woPlanner's live Race tyre-MC updates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from wodata.live import LiveSessionStore
from wodata.live.recorder import live_recording_path
from wodata.live.recovery import (
    RecordedMessage,
    discover_live_recordings,
    load_recording,
    merge_recordings,
    validate_recording_identity,
)
from wodata.live.replay import REQUIRED_TOPICS, TELEMETRY_TOPICS, load_replay
from wodata.live.replay.schema import parse_timestamp
from wodata.live.models import LiveTimingMessage
from wodata.script.fetch_replay_data import (
    default_archive_output_root,
    run_fetch_replay_data,
)
from woplanner.analysis.service import (
    OfflineAnalysisService,
    leader_lap_snapshots,
)


REPLAY_MODES = frozenset({"algorithm_only", "operational"})
LIVE_DEFAULT_SAMPLE_COUNT = 5000
OVERRIDE_TOPICS = frozenset(
    {"manualtyreoverride", "tyremodeloverride", "tyreoverride"}
)


@dataclass(frozen=True)
class ReplayInput:
    messages: tuple[LiveTimingMessage, ...]
    source_kind: str
    source_paths: tuple[str, ...]
    downloaded: bool
    reconstructed_from_fragments: bool
    final_leader_lap: int


def reconstruct_live_mc_history(
    *,
    year: int,
    round_number: int,
    total_laps: int,
    data_root: str | Path | None,
    mode: str,
    pre_race_tyre: Mapping[str, Any],
    figures_dir: str | Path,
    retro_tyre_estimate: Mapping[str, Any] | None = None,
    downloader=run_fetch_replay_data,
    analysis_service_factory=OfflineAnalysisService,
    analysis_options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay one completed Race through woPlanner's unchanged live-MC API."""
    mode = str(mode).lower()
    if mode not in REPLAY_MODES:
        raise ValueError(f"Unsupported live replay mode: {mode}")
    replay = obtain_complete_replay(
        year=year,
        round_number=round_number,
        total_laps=total_laps,
        data_root=data_root,
        downloader=downloader,
    )
    options = _live_options(replay.source_paths)
    if analysis_options:
        options.update(dict(analysis_options))
    service = analysis_service_factory(data_root)
    snapshots = _chronological_snapshots(replay.messages)
    baseline = _pre_race_coordinates(pre_race_tyre)
    manual_state: dict[tuple[str, str], float] = {}
    manual_active = False
    updates: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    override_cursor = 0
    override_events = _manual_override_events(replay.messages)

    for leader_lap, timestamp, session_time_seconds, snapshot, message_index in snapshots:
        while override_cursor < len(override_events):
            event_index, active, values = override_events[override_cursor]
            if event_index > message_index:
                break
            manual_active = active
            if values is not None:
                manual_state = values
            override_cursor += 1
        try:
            result = service.recalculate_from_live_snapshot(
                year=year,
                round_number=round_number,
                snapshot=snapshot,
                scheduled_total_laps_override=total_laps,
                persist=False,
                **options,
            )
            attempt = {
                "leader_lap": leader_lap,
                "timestamp": timestamp,
                "session_time_seconds": session_time_seconds,
                "status": result.status,
                "eligible_lap_count": result.eligible_lap_count,
                "input_completed_lap_count": len(snapshot.completed_laps),
                "max_input_lap": max(
                    (int(lap.lap_number) for lap in snapshot.completed_laps), default=0
                ),
            }
            attempts.append(attempt)
            if result.status != "updated":
                continue
            summary = service.load_model(year, round_number)
            updates.append(
                _history_update(
                    summary=summary,
                    leader_lap=leader_lap,
                    timestamp=timestamp,
                    session_time_seconds=session_time_seconds,
                    result=result,
                    baseline=baseline,
                    mode=mode,
                    manual_active=manual_active,
                    manual_values=manual_state,
                )
            )
        except Exception as exc:
            attempts.append(
                {
                    "leader_lap": leader_lap,
                    "timestamp": timestamp,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "input_completed_lap_count": len(snapshot.completed_laps),
                    "max_input_lap": max(
                        (int(lap.lap_number) for lap in snapshot.completed_laps), default=0
                    ),
                }
            )
    if not updates:
        failures = [str(item.get("error")) for item in attempts if item.get("error")]
        detail = failures[-1] if failures else "all updates were skipped for lack of clean laps"
        raise RuntimeError(
            "Complete replay produced no successful live tyre-MC updates; " + detail
        )

    payload = {
        "schema_version": 1,
        "year": int(year),
        "round_number": int(round_number),
        "mode": mode,
        "calculation_owner": "woPlanner OfflineAnalysisService / woStrategy live Retro MC",
        "algorithm_duplicated_in_woweekend": False,
        "causality": {
            "policy": "leader_lap_completion_clock_cut",
            "full_race_cache_used_as_lap_input": False,
            "scheduled_total_laps_is_event_metadata_not_observed_future_laps": True,
        },
        "replay": {
            "source_kind": replay.source_kind,
            "source_paths": list(replay.source_paths),
            "downloaded": replay.downloaded,
            "reconstructed_from_fragments": replay.reconstructed_from_fragments,
            "complete": replay.final_leader_lap >= int(total_laps),
            "final_leader_lap": replay.final_leader_lap,
            "expected_total_laps": int(total_laps),
        },
        "calculation_options": options,
        "manual_override_history_available": bool(override_events),
        "update_attempts": attempts,
        "updates": updates,
        "provenance": {
            "replay_mode": mode,
            "calculation_api": "OfflineAnalysisService.recalculate_from_live_snapshot",
            "snapshot_api": "woplanner.analysis.service.leader_lap_snapshots",
        },
    }
    figures = plot_live_mc_history(
        payload,
        figures_dir=figures_dir,
        retro_tyre_estimate=retro_tyre_estimate or {},
    )
    payload["figures"] = [str(path) for path in figures]
    return payload


def obtain_complete_replay(
    *, year: int, round_number: int, total_laps: int, data_root, downloader
) -> ReplayInput:
    recording_dir = live_recording_path(
        year=year,
        round_number=round_number,
        session="R",
        data_root=None if data_root is None else Path(data_root),
    ).parent
    accepted: list[list[RecordedMessage]] = []
    accepted_paths: list[str] = []
    for path in discover_live_recordings(recording_dir):
        try:
            recording = load_recording(path)
            mismatch = validate_recording_identity(
                recording, year=year, round_number=round_number, session="R"
            )
            if mismatch is None:
                accepted.append(recording)
                accepted_paths.append(str(path))
        except (OSError, ValueError, TypeError):
            continue
    if accepted:
        merged = merge_recordings(accepted)
        messages = tuple(item.message for item in merged)
        leader_lap, tyre_lap_count = _replay_completeness(messages)
        if leader_lap >= int(total_laps) and tyre_lap_count >= int(total_laps):
            return ReplayInput(
                messages,
                "live_record_fragments",
                tuple(accepted_paths),
                False,
                len(accepted) > 1,
                leader_lap,
            )

    archive_replay = (
        default_archive_output_root(data_root)
        / "schema_v1"
        / f"year={year}"
        / f"round={round_number}"
        / "session=R"
        / "replay"
        / "merged_replay_v1.jsonl"
    )
    if archive_replay.is_file():
        messages = _archive_messages(archive_replay)
        leader_lap, tyre_lap_count = _replay_completeness(messages)
        if leader_lap >= int(total_laps) and tyre_lap_count >= int(total_laps):
            return ReplayInput(
                messages, "archive_replay", (str(archive_replay),), False, False, leader_lap
            )

    result = downloader(
        {
            "year": int(year),
            "round_number": int(round_number),
            "event": None,
            "session": "R",
            "data_root": data_root,
            "download": True,
            "build": True,
            "play": False,
            "topics": (*REQUIRED_TOPICS, *TELEMETRY_TOPICS),
            "mandatory_topics": TELEMETRY_TOPICS,
            "update_existing": True,
            "overwrite": False,
            "strict_build": True,
        }
    )
    if result.replay_path is None or not Path(result.replay_path).is_file():
        raise RuntimeError("Replay download completed without a replay artifact")
    messages = _archive_messages(Path(result.replay_path))
    leader_lap, tyre_lap_count = _replay_completeness(messages)
    if leader_lap < int(total_laps) or tyre_lap_count < int(total_laps):
        raise RuntimeError(
            "Downloaded replay is incomplete: "
            f"leader lap {leader_lap}/{total_laps}, tyre laps {tyre_lap_count}"
        )
    return ReplayInput(
        messages, "downloaded_archive_replay", (str(result.replay_path),), True, False,
        leader_lap,
    )


def _archive_messages(path: Path) -> tuple[LiveTimingMessage, ...]:
    events, invalid = load_replay(path)
    if invalid:
        raise ValueError(f"Replay contains {invalid} invalid records")
    return tuple(
        LiveTimingMessage(
            topic=event.topic,
            payload=event.payload,
            source_timestamp_utc=parse_timestamp(event.original_timestamp),
            received_at_utc=parse_timestamp(event.original_timestamp)
            or datetime.fromtimestamp(event.session_time_seconds, timezone.utc),
            schema_version=event.schema_version,
            source="f1_archive_replay",
            sequence=event.sequence,
            session_time_seconds=event.session_time_seconds,
        )
        for event in events
    )


def _final_leader_lap(messages: Iterable[LiveTimingMessage]) -> int:
    return _replay_completeness(messages)[0]


def _replay_completeness(messages: Iterable[LiveTimingMessage]) -> tuple[int, int]:
    store = LiveSessionStore(allow_retirement_reactivation=True)
    store.apply_many(list(messages))
    snapshot = store.snapshot()
    tyre_laps = sum(
        lap.compound is not None and lap.tyre_life is not None and lap.stint is not None
        for lap in snapshot.completed_laps
    )
    return _leader_lap(snapshot), int(tyre_laps)


def _leader_lap(snapshot) -> int:
    leaders = [int(lap.lap_number) for lap in snapshot.completed_laps if lap.position == 1]
    all_laps = [int(lap.lap_number) for lap in snapshot.completed_laps]
    return max(leaders or all_laps or [0])


def _chronological_snapshots(messages: tuple[LiveTimingMessage, ...]):
    store = LiveSessionStore(allow_retirement_reactivation=True)
    previous = 0
    output = []
    for index, message in enumerate(messages):
        # Snapshot construction materialises all completed timing state.  Do
        # the cheap reducer mutation for every event and materialise only when
        # an event can advance a completed-lap counter.
        store.reduce(message)
        if _observed_lap(message) <= previous:
            continue
        snapshot = store.snapshot()
        current = _leader_lap(snapshot)
        if current <= previous:
            continue
        timestamp = message.source_timestamp_utc or message.received_at_utc
        for leader_lap, causal in leader_lap_snapshots(
            snapshot, first_lap=previous + 1, last_lap=current
        ):
            output.append(
                (
                    leader_lap,
                    timestamp.isoformat(),
                    message.session_time_seconds,
                    causal,
                    index,
                )
            )
        previous = current
    return output


def _observed_lap(message: LiveTimingMessage) -> int:
    payload = message.payload
    if not isinstance(payload, Mapping):
        return 0
    values = []
    if message.topic == "LapCount":
        values.append(payload.get("CurrentLap"))
    elif message.topic == "TimingData":
        lines = payload.get("Lines", payload)
        if isinstance(lines, Mapping):
            values.extend(
                row.get("NumberOfLaps")
                for row in lines.values()
                if isinstance(row, Mapping)
            )
    parsed = []
    for value in values:
        try:
            parsed.append(int(value))
        except (TypeError, ValueError):
            pass
    return max(parsed, default=0)


def _live_options(source_paths: tuple[str, ...]) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "sample_count": LIVE_DEFAULT_SAMPLE_COUNT,
        "quick_lap_threshold": 1.10,
        "min_clean_laps": 4,
        "allowed_compounds": ("SOFT", "MEDIUM", "HARD"),
        "exclude_stint_start_laps": 0,
        "exclude_stint_end_laps": 0,
        "use_sector_boundary_traffic": False,
        "min_gap_ahead_seconds": 2.5,
        "min_gap_behind_seconds": 1.0,
        "enforce_compound_order": True,
        "race_baseline_architecture": "retro",
    }
    for source in source_paths:
        state_path = Path(source).parent / "strategy_prediction_state.json"
        if not state_path.is_file():
            continue
        import json

        controls = dict(json.loads(state_path.read_text(encoding="utf-8")).get("controls") or {})
        defaults.update(
            {
                "sample_count": int(controls.get("live_sample_count", defaults["sample_count"])),
                "quick_lap_threshold": float(controls.get("quick_threshold", defaults["quick_lap_threshold"])),
                "min_clean_laps": int(controls.get("min_clean_laps", defaults["min_clean_laps"])),
                "allowed_compounds": tuple(controls.get("clean_compounds", defaults["allowed_compounds"])),
                "exclude_stint_start_laps": int(controls.get("exclude_stint_start_laps", 0)),
                "exclude_stint_end_laps": int(controls.get("exclude_stint_end_laps", 0)),
                "use_sector_boundary_traffic": controls.get("traffic_source") == "sector",
                "min_gap_ahead_seconds": float(controls.get("min_gap_ahead_seconds", 2.5)),
                "min_gap_behind_seconds": float(controls.get("min_gap_behind_seconds", 1.0)),
                "enforce_compound_order": bool(controls.get("enforce_compound_order", True)),
            }
        )
        break
    return defaults


def _pre_race_coordinates(payload: Mapping[str, Any]) -> dict[tuple[str, str], float]:
    output: dict[tuple[str, str], float] = {}
    for compound, row in dict(payload.get("compounds") or {}).items():
        if not isinstance(row, Mapping):
            continue
        for parameter, key in (
            ("degradation", "degradation_seconds_per_lap"),
            ("compound_delta", "performance_delta_to_medium"),
        ):
            value = row.get(key)
            if isinstance(value, Mapping):
                value = value.get("effective_value")
            if value is not None:
                output[(parameter, str(compound).upper())] = float(value)
    return output


def _history_update(
    *, summary, leader_lap, timestamp, session_time_seconds=None, result, baseline, mode,
    manual_active, manual_values
) -> dict[str, Any]:
    parameter_map = {
        (str(row.get("parameter")), str(row.get("compound") or "").upper()): row
        for row in summary.parameters
        if str(row.get("source_scope", "aggregate")).upper() == "AGGREGATE"
    }
    quality = dict(summary.manifest.get("sampler_quality") or {})
    observed = {str(value).upper() for value in quality.get("observed_compounds", ())}
    compounds = {}
    for compound in ("SOFT", "MEDIUM", "HARD"):
        values = {}
        for parameter in ("degradation", "compound_delta"):
            row = parameter_map.get((parameter, compound))
            calculated = None if row is None else float(row["median"])
            algorithm_value = calculated if compound in observed else baseline.get((parameter, compound))
            manual_value = manual_values.get((parameter, compound)) if manual_active else None
            effective = (
                manual_value
                if mode == "operational" and manual_value is not None
                else algorithm_value
            )
            values[parameter] = {
                "calculated_value": calculated,
                "algorithm_value": algorithm_value,
                "manual_override": {
                    "active": bool(mode == "operational" and manual_active and manual_value is not None),
                    "value": manual_value if mode == "operational" else None,
                },
                "effective_value": effective,
                "uncertainty": None if row is None else {
                    "p10": float(row["p10"]),
                    "p90": float(row["p90"]),
                    "p10_p90_width": float(row["p90"]) - float(row["p10"]),
                },
                "informed": compound in observed and row is not None,
                "support_status": None if row is None else row.get("support_status"),
            }
        compounds[compound] = values
    weighted_rmse = quality.get("weighted_rmse_seconds")
    if weighted_rmse is None:
        weighted_rmse = next(
            (row.get("weighted_rmse") for row in summary.parameters if row.get("weighted_rmse") is not None),
            None,
        )
    return {
        "leader_lap": int(leader_lap),
        "timestamp": timestamp,
        "session_time_seconds": session_time_seconds,
        "analysis_id": result.analysis_id,
        "eligible_lap_count": result.eligible_lap_count,
        "compounds": compounds,
        "quality": {
            "weighted_rmse_seconds": weighted_rmse,
            "best_rmse_seconds": quality.get("best_rmse_seconds"),
            "ess": quality.get("ess"),
            "ess_fraction": quality.get("ess_fraction"),
            "sample_count": quality.get("sample_count", result.sample_count),
            "numerical_quality_status": quality.get("numerical_quality_status"),
            "clean_race_laps": quality.get("clean_race_laps"),
            "clean_race_runs": quality.get("clean_race_runs"),
        },
        "identifiability": {
            key: value for key, value in quality.items()
            if "identif" in str(key) or str(key).startswith("fuel_track_")
        },
        "provenance": {
            "input_fingerprint": summary.manifest.get("input_fingerprint"),
            "config_hash": summary.manifest.get("config_hash"),
            "calculation_api": "OfflineAnalysisService.recalculate_from_live_snapshot",
            "manual_override_mode": mode,
        },
    }


def _manual_override_events(messages: tuple[LiveTimingMessage, ...]):
    events = []
    current: dict[tuple[str, str], float] = {}
    for index, message in enumerate(messages):
        if message.topic.lower().replace("_", "") not in OVERRIDE_TOPICS:
            continue
        payload = message.payload
        if not isinstance(payload, Mapping):
            continue
        active = bool(payload.get("active", True))
        raw = payload.get("values", payload)
        values = dict(current)
        if isinstance(raw, Mapping):
            aliases = {
                "Deg_SOFT": ("degradation", "SOFT"),
                "Deg_MEDIUM": ("degradation", "MEDIUM"),
                "Deg_HARD": ("degradation", "HARD"),
                "Perf_SOFT_vs_MEDIUM": ("compound_delta", "SOFT"),
                "Perf_HARD_vs_MEDIUM": ("compound_delta", "HARD"),
            }
            for key, coordinate in aliases.items():
                if raw.get(key) is not None:
                    values[coordinate] = float(raw[key])
            for parameter in ("degradation", "compound_delta"):
                nested = raw.get(parameter)
                if isinstance(nested, Mapping):
                    for compound, value in nested.items():
                        if value is not None:
                            values[(parameter, str(compound).upper())] = float(value)
        current = values
        events.append((index, active, dict(values)))
    return events


def plot_live_mc_history(
    history: Mapping[str, Any], *, figures_dir: str | Path, retro_tyre_estimate
) -> tuple[Path, Path]:
    import matplotlib.pyplot as plt
    import numpy as np

    destination = Path(figures_dir)
    destination.mkdir(parents=True, exist_ok=True)
    updates = list(history.get("updates") or [])
    colours = {"SOFT": "#e10600", "MEDIUM": "#d9a900", "HARD": "#777777"}
    degradation_path = destination / "live_mc_degradation_evolution.png"
    quality_path = destination / "live_mc_model_quality_evolution.png"

    figure, axis = plt.subplots(figsize=(10, 5.5))
    for compound in ("SOFT", "MEDIUM", "HARD"):
        points = [
            (
                int(update["leader_lap"]),
                update["compounds"][compound]["degradation"]["algorithm_value"],
                bool(update["compounds"][compound]["degradation"]["informed"]),
            )
            for update in updates
            if update["compounds"][compound]["degradation"]["algorithm_value"] is not None
        ]
        for informed, style in ((False, "--"), (True, "-")):
            selected = [point for point in points if point[2] is informed]
            if selected:
                axis.plot(
                    [point[0] for point in selected], [point[1] for point in selected],
                    style, marker="o", color=colours[compound],
                    label=f"{compound} ({'informed' if informed else 'assumed'})",
                )
        retro = dict(retro_tyre_estimate.get("compounds") or {}).get(compound, {})
        if isinstance(retro, Mapping) and retro.get("degradation_seconds_per_lap") is not None:
            axis.axhline(
                float(retro["degradation_seconds_per_lap"]), color=colours[compound],
                linestyle=":", alpha=.65,
            )
    axis.set(title="Tyre degradation evolution", xlabel="Leader Lap", ylabel="Degradation (s/lap)")
    axis.grid(alpha=.25)
    axis.legend(ncol=2, fontsize=8)
    figure.tight_layout()
    figure.savefig(degradation_path, dpi=160)
    plt.close(figure)

    figure, rmse_axis = plt.subplots(figsize=(10, 5.5))
    laps = [int(update["leader_lap"]) for update in updates]
    weighted = [update["quality"].get("weighted_rmse_seconds") for update in updates]
    best = [update["quality"].get("best_rmse_seconds") for update in updates]
    if any(value is not None for value in weighted):
        rmse_axis.plot(laps, weighted, "o-", color="#d62728", label="Weighted RMSE")
    if any(value is not None for value in best):
        rmse_axis.plot(laps, best, "o--", color="#ff9896", label="Best RMSE")
    rmse_axis.set(xlabel="Leader Lap", ylabel="RMSE (s)", title="Live MC model quality evolution")
    ess_axis = rmse_axis.twinx()
    ess = [update["quality"].get("ess") for update in updates]
    if any(value is not None and float(value) > 0 for value in ess):
        ess_axis.plot(laps, [np.nan if value is None else value for value in ess], "o-", color="#3366cc", label="ESS")
    ess_axis.set_ylabel("Effective Sample Size (ESS)")
    handles1, labels1 = rmse_axis.get_legend_handles_labels()
    handles2, labels2 = ess_axis.get_legend_handles_labels()
    rmse_axis.legend(handles1 + handles2, labels1 + labels2, loc="best")
    rmse_axis.grid(alpha=.25)
    figure.tight_layout()
    figure.savefig(quality_path, dpi=160)
    plt.close(figure)
    return degradation_path, quality_path
