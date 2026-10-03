"""Acquire and prepare existing producer inputs before race analysis starts."""

from __future__ import annotations

from pathlib import Path

from wodata import get_data_root, get_fastf1_raw_cache_dir
from wodata.events import resolve_event
from wodata.pirelli_preview import preview_json_path, read_preview_json, sha256_file
from wodata.script.fetch_replay_data import SCRIPT_CONFIG, run_fetch_replay_data
from wostrategy.analysis.pre_race_model_config import (
    ensure_pre_race_model_config,
    resolve_practice_sessions,
)
from wostrategy.analysis.pre_race_tyre_prediction import get_pre_race_tyre_prediction
from wostrategy.analysis.tyre_prediction_pipeline import run_tyre_prediction_pipeline
from wostrategy.core.pre_race_session_data import load_cached_weekend_sessions

from .common import resolve_workflow_event
from .race import RacePreparationPrerequisiteError


def prepare_race_prerequisites(
    config, *, event: str, event_resolver=resolve_event,
    fp_fetcher=run_fetch_replay_data, session_loader=load_cached_weekend_sessions,
    tyre_runner=run_tyre_prediction_pipeline,
    tyre_provider=get_pre_race_tyre_prediction,
    model_config_ensurer=ensure_pre_race_model_config,
):
    """Run the canonical prerequisite stages; never download/extract Pirelli info.

    All stages finish before an immutable race run or report can be created.
    Injectable producers are also used by the orchestration tests.
    """
    root = get_data_root(config.data_root).resolve()
    if event_resolver is resolve_event:
        import fastf1

        raw_cache = get_fastf1_raw_cache_dir(root)
        raw_cache.mkdir(parents=True, exist_ok=True)
        fastf1.Cache.enable_cache(str(raw_cache))
    try:
        identity = resolve_workflow_event(
            event, config, include_race_distance=False, resolver=event_resolver,
        )
        sessions = resolve_practice_sessions(identity.session_names)
    except Exception as exc:
        raise RacePreparationPrerequisiteError(
            f"{event} prerequisite stage 'event metadata' failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    def stage(name, call):
        print(f"[race prerequisites] {identity.event_id}: {name}", flush=True)
        try:
            return call()
        except Exception as exc:
            raise RacePreparationPrerequisiteError(
                f"{identity.event_id} prerequisite stage '{name}' failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    preview_path = preview_json_path(identity.season, identity.round_number, root)

    def processed_preview():
        if not preview_path.is_file():
            raise FileNotFoundError(
                f"Missing processed Pirelli input: {preview_path}. Download/process "
                "the Pirelli preview manually with Codex and save an accepted "
                "woData preview JSON before running woweekend-race."
            )
        record = read_preview_json(preview_path)
        if (record.season, record.round) != (identity.season, identity.round_number):
            raise ValueError(f"Processed Pirelli input belongs to another event: {preview_path}")
        if record.review_status != "accepted":
            raise ValueError(f"Processed Pirelli input needs manual review: {preview_path}")
        image_path = preview_path.parent.parent / Path(record.source_file).name
        if image_path.is_file() and sha256_file(image_path) != record.source_hash:
            raise ValueError(
                f"Pirelli image changed since processing: {image_path}. "
                "Process it manually again before running woweekend-race."
            )
        return {"path": str(preview_path), "source_hash": record.source_hash}

    preview = stage("processed Pirelli input", processed_preview)
    archives = {}
    for session in sessions:
        values = dict(SCRIPT_CONFIG)
        values.update(
            year=identity.season, round_number=identity.round_number,
            event=None, session=session, data_root=root,
            download=True, build=False, play=False, overwrite=False,
        )
        fetched = stage(f"fetch {session} data", lambda: fp_fetcher(values))
        archives[session] = {
            "path": str(fetched.archive_dir), "reused": fetched.reused_download,
            "producer_api": "wodata.script.fetch_replay_data.run_fetch_replay_data",
        }

    def enriched_sessions():
        loaded = session_loader(
            year=identity.season, round_number=identity.round_number,
            sessions=sessions, data_root=root,
        )
        missing = [
            session for session in sessions
            if session not in loaded.sessions or loaded.sessions[session].empty
        ]
        if missing:
            details = "; ".join(
                f"{session}: {loaded.sources.get(session, 'unavailable')} "
                f"(cache: {loaded.cache_paths.get(session)})"
                for session in missing
            )
            raise RuntimeError(f"Required FP lap data unavailable: {details}")
        return {
            session: {"source": loaded.sources[session],
                      "path": str(loaded.cache_paths[session]),
                      "lap_count": len(loaded.sessions[session])}
            for session in sessions
        }

    laps = stage("ensure enriched FP lap data", enriched_sessions)

    def tyre_prediction():
        report = tyre_runner(
            season=identity.season, data_root=root, processed_previews_only=True,
            refresh_fp_calibration=False,
        )
        if identity.round_number not in {item.round_number for item in report.predictions}:
            readiness_root = (
                report.training_data_path.parent / "predictions"
                / f"round={identity.round_number}"
            )
            raise RuntimeError(
                "woStrategy did not produce the target tyre prediction. Check accepted "
                "historical Pirelli records, existing Race Retro summaries and model "
                f"readiness under {readiness_root}."
            )
        prediction = tyre_provider(
            season=identity.season, round_number=identity.round_number, data_root=root,
        )
        return {
            "path": prediction.provenance["path"],
            "artifact_id": prediction.artifact_id,
            "historical_fp_calibration_refresh": "separate_diagnostic_workflow",
            "producer_api": "wostrategy.analysis.tyre_prediction_pipeline.run_tyre_prediction_pipeline",
        }

    tyre = stage("pre-race tyre prediction", tyre_prediction)
    ensured = stage("pre-race FP model", lambda: model_config_ensurer(
        season=identity.season, round_number=identity.round_number, data_root=root,
        session_names=identity.session_names, require_fp_evidence=True,
    ))
    model_config = ensured.to_dict() if hasattr(ensured, "to_dict") else dict(ensured)
    return {
        "data_root": str(root), "event_id": identity.event_id,
        "sessions": list(sessions), "processed_pirelli": preview,
        "fp_archives": archives, "fp_laps": laps,
        "tyre_prediction": tyre, "live_mc_model_config": model_config,
    }
