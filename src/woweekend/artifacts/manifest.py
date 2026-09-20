from __future__ import annotations

from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version
import hashlib
import subprocess
from pathlib import Path
from typing import Any, Mapping

from wodata.contracts import canonical_hash

from .models import ComponentResult, overall_status


def build_manifest(
    *,
    workflow: str,
    event: str,
    run_id: str,
    components: Mapping[str, ComponentResult],
    config_path: str = "config.resolved.json",
    run_path: str | Path | None = None,
    workspace_root: str | Path | None = None,
) -> dict[str, object]:
    warnings = [warning for item in components.values() for warning in item.warnings]
    warnings.extend(f"{name}: {item.error}" for name, item in components.items() if item.error)
    outputs = {name: item.status for name, item in components.items()}
    provenance = {
        name: dict(item.provenance)
        for name, item in components.items()
        if item.provenance
    }
    code_identity = _code_identities(workspace_root)
    return {
        "workflow": workflow,
        "event": event,
        "run_id": run_id,
        "status": overall_status(components),
        "config": config_path,
        "inputs": _run_inputs(run_path),
        "outputs": outputs,
        "output_provenance": provenance,
        "warnings": warnings,
        "package_versions": _versions(),
        "git_commits": {
            name: str(identity.get("head") or "unavailable")
            for name, identity in code_identity.items()
        },
        "consumer_code_identity": code_identity,
        "provenance_scope": {
            "config_and_input_references": "immutable_run_files_at_finalization",
            "code_identity": "consumer_worktrees_at_run_finalization",
            "cached_artifact_producer_identity": (
                "preserved only when supplied by the artifact; never inferred from consumer"
            ),
            "complete_as_run_claim": False,
        },
    }


def _versions() -> dict[str, str]:
    output = {}
    for package in ("woweekend", "wodata", "wostrategy", "wostanding", "woplanner"):
        try:
            output[package] = version(package)
        except PackageNotFoundError:
            output[package] = "editable/uninstalled"
    return output


def _repository_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _workspace_root(value: str | Path | None = None) -> Path:
    if value is not None:
        return Path(value).resolve()
    repository = _repository_root(Path(__file__).resolve())
    return repository.parent if repository is not None else Path(__file__).resolve().parents[4]


def _code_identities(workspace_root: str | Path | None = None) -> dict[str, object]:
    workspace = _workspace_root(workspace_root)
    output: dict[str, object] = {}
    for repository in ("woWeekend", "woData", "woStrategy", "woStanding", "woPlanner"):
        directory = workspace / repository
        if not (directory / ".git").exists():
            continue
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=directory, check=True,
                text=True, capture_output=True,
            ).stdout.strip()
            status = subprocess.run(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                cwd=directory, check=True, text=True, capture_output=True,
            ).stdout.splitlines()
            output[repository] = {
                "head": head,
                "dirty": bool(status),
                "changed_paths": [line[3:] for line in status if len(line) > 3],
                "identity_scope": "consumer_worktree_at_run_finalization",
            }
        except (OSError, subprocess.SubprocessError) as exc:
            output[repository] = {
                "head": None,
                "dirty": None,
                "changed_paths": [],
                "identity_scope": "consumer_worktree_at_run_finalization",
                "status": f"unavailable: {type(exc).__name__}",
            }
    return output


def _git_commits(workspace_root: str | Path | None = None) -> dict[str, str]:
    return {
        name: str(identity.get("head") or "unavailable")
        for name, identity in _code_identities(workspace_root).items()
    }


def _run_inputs(run_path: str | Path | None) -> dict[str, object]:
    if run_path is None:
        return {}
    root = Path(run_path)
    output: dict[str, object] = {}
    for name in ("config.input.json", "config.resolved.json", "event.metadata.json"):
        path = root / name
        if not path.is_file():
            continue
        output[name] = {
            "uri": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "identity_scope": "immutable_run_input",
        }
    return output


def resolved_model_config_provenance(
    source: Mapping[str, Any], resolved: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Keep cached producer identity separate from the config this run consumed."""
    output = dict(source)
    output["artifact_role"] = "consumed_model_config"
    producer_identity = source.get("producer_code_identity")
    if producer_identity is None and source.get("status") == "generated":
        producer_identity = {
            "status": "current_run",
            "reference": "run manifest consumer_code_identity",
        }
    output["producer_code_identity"] = producer_identity or {
        "status": "unavailable_for_cached_or_legacy_artifact"
    }
    if resolved is None:
        output["consumer_resolution"] = {"status": "unavailable"}
    else:
        content = dict(resolved)
        output["consumer_resolution"] = {
            "status": "resolved",
            "resolved_artifact_content": content,
            "resolved_artifact_content_hash": canonical_hash(content),
            "hash_scope": "full_resolved_model_config_content",
        }
    return output


def calculation_input_identity(
    effective_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Fingerprint effective replay configuration and control values only.

    The service capture deliberately retains ordinary input provenance.  The
    known provenance substructure (and its legacy location) is excluded here;
    behavior-affecting mode, manual events, options, tyre values and numeric
    event context remain part of the identity.
    """
    content = deepcopy(dict(effective_inputs))
    service = content.get("service_resolved_calculation")
    if isinstance(service, Mapping):
        resolved = dict(service)
        input_provenance = resolved.get("input_provenance")
        if isinstance(input_provenance, Mapping):
            remaining_provenance = {
                key: value
                for key, value in input_provenance.items()
                if key not in {
                    "scheduled_total_laps_source", "path", "description"
                }
            }
            if remaining_provenance:
                resolved["input_provenance"] = remaining_provenance
            else:
                resolved.pop("input_provenance", None)
        event_context = resolved.get("event_context")
        if isinstance(event_context, Mapping):
            effective_event_context = dict(event_context)
            effective_event_context.pop("scheduled_total_laps_source", None)
            resolved["event_context"] = effective_event_context
        content["service_resolved_calculation"] = resolved
    return {
        "hash_scope": "effective_replay_configuration_and_control_inputs_v1",
        "scope_definition": (
            "resolved calculation configuration/control values, effective tyre "
            "inputs, behavior-affecting manual events, and necessary event context; "
            "excludes observation stream, sample bank, descriptive input provenance, "
            "and complete as-run identity"
        ),
        "content": content,
        "fingerprint": canonical_hash(content),
    }


def _calculation_input_provenance(
    effective_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    service = effective_inputs.get("service_resolved_calculation")
    if not isinstance(service, Mapping):
        return {}
    captured = service.get("input_provenance")
    if isinstance(captured, Mapping):
        return {"service_resolved_calculation": dict(captured)}
    event_context = service.get("event_context")
    if isinstance(event_context, Mapping):
        source = event_context.get("scheduled_total_laps_source")
        if source is not None:
            return {
                "service_resolved_calculation": {
                    "scheduled_total_laps_source": source,
                    "capture_layout": "legacy_event_context",
                }
            }
    return {}


def replay_provenance(
    *,
    mode: str,
    model_config: Mapping[str, Any],
    calculation_options: Mapping[str, Any],
    pre_race_tyre: Mapping[str, Any],
    effective_calculation_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Describe a replay's actual inputs without claiming historical as-run state."""
    return {
        "replay_mode": mode,
        "replay_classification": mode,
        "complete_as_run_reconstruction": False,
        "evidence_cutoff": "leader_lap_completion_clock_cut",
        "calculation_api": "OfflineAnalysisService.recalculate_from_live_snapshot",
        "snapshot_api": "woplanner.analysis.service.leader_lap_snapshots",
        "model_config": dict(model_config),
        "calculation_options": {
            "resolved_content": dict(calculation_options),
            "resolved_content_hash": canonical_hash(calculation_options),
            "hash_scope": "full_replay_calculation_options_content",
        },
        "effective_calculation_inputs": calculation_input_identity(
            effective_calculation_inputs
        ),
        "calculation_input_provenance": _calculation_input_provenance(
            effective_calculation_inputs
        ),
        "pre_race_tyre_input": {
            "artifact_id": pre_race_tyre.get("automatic_artifact_id")
            or pre_race_tyre.get("manual_fallback_artifact_id"),
            "artifact_content_hash": canonical_hash(pre_race_tyre),
            "artifact_hash_scope": "full_pre_race_tyre_artifact_content",
            "producer_code_identity": (
                pre_race_tyre.get("producer_code_identity")
                or {"status": "unavailable_for_legacy_artifact"}
            ),
        },
    }
