from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass
class ComponentResult:
    status: str
    output: Any = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def overall_status(components: Mapping[str, ComponentResult]) -> str:
    statuses = [component.status.upper() for component in components.values()]
    if statuses and all(status == "SUCCESS" for status in statuses):
        return "SUCCESS"
    if any(status == "SUCCESS" for status in statuses):
        return "PARTIAL"
    return "FAILED"
