from __future__ import annotations

from dataclasses import asdict, dataclass
import difflib
import json
from pathlib import Path
from typing import Any, Mapping


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ReportConfig:
    language: str = "zh-CN"
    template: str = "engineering"

    @classmethod
    def parse(cls, value: object) -> "ReportConfig":
        data = clean_mapping(value, path="report")
        reject_unknown(data, {"language", "template"}, path="report")
        language = str(data.get("language", "zh-CN"))
        if language not in {"zh-CN", "en-GB"}:
            raise ConfigError("report.language must be one of: zh-CN, en-GB")
        template = str(data.get("template", "engineering"))
        if template != "engineering":
            raise ConfigError("report.template must be 'engineering' in V1")
        return cls(language, template)


def clean_mapping(value: object, *, path: str = "config") -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must be a JSON object")
    return {
        str(key): strip_metadata(item)
        for key, item in value.items()
        if not str(key).startswith("_")
    }


def strip_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): strip_metadata(item)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    if isinstance(value, list):
        return [strip_metadata(item) for item in value]
    return value


def reject_unknown(data: Mapping[str, Any], allowed: set[str], *, path: str) -> None:
    unknown = sorted(set(data).difference(allowed))
    if not unknown:
        return
    messages = []
    for key in unknown:
        suggestion = difflib.get_close_matches(key, allowed, n=1, cutoff=0.6)
        messages.append(f"{path}.{key}" + (f" (did you mean {suggestion[0]!r}?)" if suggestion else ""))
    raise ConfigError("Unknown configuration field(s): " + ", ".join(messages))


def require_int(data: Mapping[str, Any], key: str, *, path: str, minimum: int = 1) -> int:
    if key not in data:
        raise ConfigError(f"Missing required field {path}.{key}")
    try:
        value = int(data[key])
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path}.{key} must be an integer") from exc
    if value < minimum:
        raise ConfigError(f"{path}.{key} must be >= {minimum}")
    return value


def optional_float(data: Mapping[str, Any], key: str, *, path: str) -> float | None:
    value = data.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path}.{key} must be a number or null") from exc


def load_json_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ConfigError("Top-level configuration must be a JSON object")
    return value


def resolved_dict(config: object) -> dict[str, Any]:
    return asdict(config)


def write_template(path: str | Path, value: Mapping[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination
