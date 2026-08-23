from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .common import ReportConfig, clean_mapping, reject_unknown, require_int


@dataclass(frozen=True)
class QualifyingConfig:
    season: int | None = None
    round_number: int | None = None
    session: str = "Q"
    push_threshold_fraction: float = 0.02
    data_root: str | None = None
    report: ReportConfig = ReportConfig()

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "QualifyingConfig":
        data = clean_mapping(value)
        reject_unknown(data, {"season", "round_number", "session", "push_threshold_fraction", "data_root", "report"}, path="config")
        session = str(data.get("session", "Q")).upper()
        if session not in {"Q", "SQ"}:
            raise ValueError("config.session must be Q or SQ")
        threshold = float(data.get("push_threshold_fraction", 0.02))
        if not 0 < threshold < 1:
            raise ValueError("config.push_threshold_fraction must be between 0 and 1")
        season = int(data["season"]) if data.get("season") is not None else None
        round_number = int(data["round_number"]) if data.get("round_number") is not None else None
        return cls(season, round_number, session, threshold, data.get("data_root"), ReportConfig.parse(data.get("report")))

    @staticmethod
    def template() -> dict[str, Any]:
        return {"_help": {"session": "Q for standard qualifying or SQ for Sprint qualifying.", "push_threshold_fraction": "Existing push-lap classifier threshold relative to the fastest eligible lap: 0.02 accepts laps within 2% of that reference. It is also used by the live planner against the fastest active reference lap.", "data_root": "Optional woData root override. Normally leave null."}, "session": "Q", "push_threshold_fraction": 0.02, "data_root": None, "report": {"_help": "Presentation only; changing language does not rerun analysis.", "language": "zh-CN", "template": "engineering"}}
