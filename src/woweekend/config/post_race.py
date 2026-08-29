from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .common import ReportConfig, clean_mapping, reject_unknown, require_int


@dataclass(frozen=True)
class AnalysisOverrides:
    overrides: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, value: object, *, path: str) -> "AnalysisOverrides":
        data = clean_mapping(value, path=path)
        reject_unknown(data, {"overrides"}, path=path)
        overrides = data.get("overrides", {})
        if not isinstance(overrides, Mapping):
            raise ValueError(f"{path}.overrides must be an object")
        return cls(dict(overrides))


@dataclass(frozen=True)
class LiveReplayConfig:
    mode: str = "algorithm_only"

    @classmethod
    def parse(cls, value: object) -> "LiveReplayConfig":
        data = clean_mapping(value, path="live_replay")
        reject_unknown(data, {"mode"}, path="live_replay")
        mode = str(data.get("mode", "algorithm_only")).strip().lower()
        if mode not in {"algorithm_only", "operational"}:
            raise ValueError(
                "live_replay.mode must be 'algorithm_only' or 'operational'"
            )
        return cls(mode)


@dataclass(frozen=True)
class PostRaceConfig:
    season: int | None = None
    round_number: int | None = None
    race_start: str | None = None
    pre_race_run_id: str | None = None
    qualifying_performance: AnalysisOverrides = field(default_factory=AnalysisOverrides)
    race_performance: AnalysisOverrides = field(default_factory=AnalysisOverrides)
    live_replay: LiveReplayConfig = field(default_factory=LiveReplayConfig)
    data_root: str | None = None
    report: ReportConfig = ReportConfig()

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "PostRaceConfig":
        data = clean_mapping(value)
        reject_unknown(data, {"season", "round_number", "race_start", "pre_race_run_id", "qualifying_performance", "race_performance", "live_replay", "data_root", "report"}, path="config")
        season = int(data["season"]) if data.get("season") is not None else None
        round_number = int(data["round_number"]) if data.get("round_number") is not None else None
        race_start = str(data["race_start"]) if data.get("race_start") is not None else None
        return cls(
            season=season,
            round_number=round_number,
            race_start=race_start,
            pre_race_run_id=data.get("pre_race_run_id"),
            qualifying_performance=AnalysisOverrides.parse(
                data.get("qualifying_performance"), path="qualifying_performance"
            ),
            race_performance=AnalysisOverrides.parse(
                data.get("race_performance"), path="race_performance"
            ),
            live_replay=LiveReplayConfig.parse(data.get("live_replay")),
            data_root=data.get("data_root"),
            report=ReportConfig.parse(data.get("report")),
        )

    @staticmethod
    def template() -> dict[str, Any]:
        return {"_help": {"race_start": "Automatically resolved from FastF1 event/session metadata. Leave null normally; set an ISO-8601 timestamp only to correct missing/incorrect metadata.", "pre_race_run_id": "Identifies the saved Race Preparation run treated as the genuine pre-race prediction. Leave null to select the final usable run before race start; set a RUN_ID only to force a specific historical run.", "post_strategy_status": "Retro Green Optimum is calculated automatically. Event-Aware Hindsight Optimum may be unavailable until its deterministic SC/VSC API is extracted from woPlanner."}, "race_start": None, "pre_race_run_id": None, "qualifying_performance": {"_help": {"overrides": "Passed only to parameters supported by the existing woStrategy qualifying performance API."}, "overrides": {}}, "race_performance": {"_help": {"overrides": "Passed only to parameters supported by the stable woStrategy race-review adapter."}, "overrides": {}}, "live_replay": {"_help": {"mode": "algorithm_only evaluates the live MC model without historical manual overrides. operational also replays saved manual overrides and reconstructs the effective values used during the race."}, "mode": "algorithm_only"}, "data_root": None, "report": {"language": "zh-CN", "template": "engineering"}}
