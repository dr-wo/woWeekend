from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
import subprocess
from pathlib import Path
from typing import Mapping

from .models import ComponentResult, overall_status


def build_manifest(*, workflow: str, event: str, run_id: str, components: Mapping[str, ComponentResult], config_path: str = "config.resolved.json") -> dict[str, object]:
    warnings = [warning for item in components.values() for warning in item.warnings]
    warnings.extend(f"{name}: {item.error}" for name, item in components.items() if item.error)
    outputs = {name: item.status for name, item in components.items()}
    return {"workflow": workflow, "event": event, "run_id": run_id, "status": overall_status(components), "config": config_path, "inputs": {}, "outputs": outputs, "warnings": warnings, "package_versions": _versions(), "git_commits": _git_commits()}


def _versions() -> dict[str, str]:
    output = {}
    for package in ("woweekend", "wodata", "wostrategy", "wostanding", "woplanner"):
        try:
            output[package] = version(package)
        except PackageNotFoundError:
            output[package] = "editable/uninstalled"
    return output


def _git_commits() -> dict[str, str]:
    workspace = Path(__file__).resolve().parents[4].parent
    output = {}
    for repository in ("woWeekend", "woData", "woStrategy", "woStanding", "woPlanner"):
        directory = workspace / repository
        if not (directory / ".git").exists():
            continue
        try:
            output[repository] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=directory, check=True, text=True, capture_output=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            output[repository] = "unavailable"
    return output
