from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from wodata.weekend_runs import WeekendRunStore
from wodata.events import resolve_event

from woweekend.artifacts.manifest import build_manifest
from woweekend.artifacts.models import ComponentResult


def resolve_workflow_event(event: str, config, *, include_race_distance: bool, resolver=resolve_event):
    metadata = resolver(event, include_race_distance=include_race_distance)
    if getattr(config, "season", None) not in (None, metadata.season):
        raise ValueError("Legacy config season conflicts with --event")
    if getattr(config, "round_number", None) not in (None, metadata.round_number):
        raise ValueError("Legacy config round_number conflicts with --event")
    return metadata


def create_run(*, event: str, workflow: str, data_root, input_config: dict[str, Any], resolved_config: dict[str, Any], event_metadata=None):
    run = WeekendRunStore.create(event=event, workflow=workflow, data_root=data_root)
    run.save_json("config.input", input_config)
    run.save_json("config.resolved", resolved_config)
    if event_metadata is not None:
        run.save_json("event.metadata", event_metadata.to_dict())
    return run


def component(call: Callable[[], Any], *, provenance: dict[str, Any] | None = None) -> ComponentResult:
    try:
        return ComponentResult("SUCCESS", call(), provenance=provenance or {})
    except Exception as exc:
        return ComponentResult("FAILED", error=f"{type(exc).__name__}: {exc}", provenance=provenance or {})


def finish_run(run, components: dict[str, ComponentResult], *, status_override: str | None = None) -> dict[str, object]:
    results = {name: item.to_dict() for name, item in components.items()}
    run.save_json("analysis_results", results)
    manifest = build_manifest(workflow=run.workflow, event=run.event, run_id=run.run_id, components=components)
    if status_override is not None:
        manifest["status"] = status_override.upper()
    run.save_manifest(manifest)
    return manifest
