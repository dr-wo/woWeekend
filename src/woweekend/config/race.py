from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .common import ReportConfig, clean_mapping, optional_float, reject_unknown, require_int


@dataclass(frozen=True)
class PitLossSplit:
    total: float | None = None
    pit_in_s3: float | None = None
    pit_out_s1: float | None = None

    @property
    def effective_total(self) -> float | None:
        if self.total is not None:
            return self.total
        return None if self.pit_in_s3 is None or self.pit_out_s1 is None else self.pit_in_s3 + self.pit_out_s1

    @classmethod
    def parse(cls, value: object, *, path: str) -> "PitLossSplit":
        data = clean_mapping(value, path=path)
        reject_unknown(data, {"total", "pit_in_s3", "pit_out_s1"}, path=path)
        total = optional_float(data, "total", path=path)
        pit_in = optional_float(data, "pit_in_s3", path=path)
        pit_out = optional_float(data, "pit_out_s1", path=path)
        if total is not None and (pit_in is not None or pit_out is not None):
            raise ValueError(
                f"{path} is ambiguous: provide either total or both pit_in_s3/pit_out_s1, not both"
            )
        if (pit_in is None) != (pit_out is None):
            raise ValueError(f"{path} requires both pit_in_s3 and pit_out_s1")
        for name, item in (("total", total), ("pit_in_s3", pit_in), ("pit_out_s1", pit_out)):
            if item is not None and item < 0:
                raise ValueError(f"{path}.{name} cannot be negative")
        return cls(total, pit_in, pit_out)


@dataclass(frozen=True)
class PitLossConfig:
    green: PitLossSplit = PitLossSplit()
    sc_vsc: PitLossSplit = PitLossSplit()

    @classmethod
    def parse(cls, value: object) -> "PitLossConfig":
        data = clean_mapping(value, path="pit_loss")
        reject_unknown(data, {"green", "sc_vsc"}, path="pit_loss")
        return cls(PitLossSplit.parse(data.get("green"), path="pit_loss.green"), PitLossSplit.parse(data.get("sc_vsc"), path="pit_loss.sc_vsc"))


@dataclass(frozen=True)
class CutoffConfig:
    scan_min: float | None = None
    scan_max: float | None = None
    coarse_step: float | None = None
    refine_tolerance: float | None = None

    @classmethod
    def parse(cls, value: object) -> "CutoffConfig":
        data = clean_mapping(value, path="degradation_cutoff")
        reject_unknown(data, {"scan_min", "scan_max", "coarse_step", "refine_tolerance"}, path="degradation_cutoff")
        return cls(*(optional_float(data, key, path="degradation_cutoff") for key in ("scan_min", "scan_max", "coarse_step", "refine_tolerance")))


@dataclass(frozen=True)
class CompoundTyreOverride:
    performance_delta_to_medium: float | None = None
    degradation_seconds_per_lap: float | None = None

    @classmethod
    def parse(cls, value: object, *, path: str) -> "CompoundTyreOverride":
        data = clean_mapping(value, path=path)
        aliases = {"performance_delta": "performance_delta_to_medium", "degradation": "degradation_seconds_per_lap"}
        data = {aliases.get(key, key): item for key, item in data.items()}
        reject_unknown(data, {"performance_delta_to_medium", "degradation_seconds_per_lap"}, path=path)
        return cls(
            optional_float(data, "performance_delta_to_medium", path=path),
            optional_float(data, "degradation_seconds_per_lap", path=path),
        )


@dataclass(frozen=True)
class TyrePredictionConfig:
    manual_override: Mapping[str, CompoundTyreOverride]

    @classmethod
    def parse(cls, value: object) -> "TyrePredictionConfig":
        data = clean_mapping(value, path="tyre_prediction")
        reject_unknown(data, {"manual_override"}, path="tyre_prediction")
        raw = clean_mapping(data.get("manual_override"), path="tyre_prediction.manual_override")
        unknown = set(raw).difference({"SOFT", "MEDIUM", "HARD"})
        if unknown:
            raise ValueError(f"Unknown tyre override compound(s): {sorted(unknown)}")
        overrides = {
            compound: CompoundTyreOverride.parse(raw.get(compound), path=f"tyre_prediction.manual_override.{compound}")
            for compound in ("SOFT", "MEDIUM", "HARD")
        }
        medium = overrides["MEDIUM"].performance_delta_to_medium
        if medium is not None and abs(medium) > 1e-12:
            raise ValueError("MEDIUM performance_delta_to_medium must remain 0")
        return cls(overrides)


@dataclass(frozen=True)
class RaceConfig:
    season: int | None = None
    round_number: int | None = None
    total_laps: int | None = None
    session: str = "R"
    max_stops: int = 3
    result_count: int = 10
    pit_loss: PitLossConfig = PitLossConfig()
    tyre_prediction: TyrePredictionConfig = field(default_factory=lambda: TyrePredictionConfig({}))
    degradation_cutoff: CutoffConfig = CutoffConfig()
    data_root: str | None = None
    report: ReportConfig = ReportConfig()

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "RaceConfig":
        data = clean_mapping(value)
        reject_unknown(data, {"season", "round_number", "total_laps", "race", "session", "max_stops", "result_count", "strategy", "tyre_prediction", "pit_loss", "degradation_cutoff", "data_root", "report"}, path="config")
        race_data = clean_mapping(data.get("race"), path="race")
        reject_unknown(race_data, {"total_laps"}, path="race")
        strategy_data = clean_mapping(data.get("strategy"), path="strategy")
        reject_unknown(strategy_data, {"max_stops", "result_count", "rules_compliant"}, path="strategy")
        session = str(data.get("session", "R")).upper()
        if session not in {"R", "SR", "S"}:
            raise ValueError("config.session must be R, SR, or S")
        total_raw = race_data.get("total_laps", data.get("total_laps"))
        total_laps = None if total_raw is None else int(total_raw)
        if total_laps is not None and total_laps <= 0:
            raise ValueError("race.total_laps must be positive or null")
        season = int(data["season"]) if data.get("season") is not None else None
        round_number = int(data["round_number"]) if data.get("round_number") is not None else None
        max_stops = int(strategy_data.get("max_stops", data.get("max_stops", 3)))
        result_count = int(strategy_data.get("result_count", data.get("result_count", 10)))
        if max_stops < 0 or result_count <= 0:
            raise ValueError("strategy.max_stops must be non-negative and result_count positive")
        return cls(season, round_number, total_laps, session, max_stops, result_count, PitLossConfig.parse(data.get("pit_loss")), TyrePredictionConfig.parse(data.get("tyre_prediction")), CutoffConfig.parse(data.get("degradation_cutoff")), data.get("data_root"), ReportConfig.parse(data.get("report")))

    @staticmethod
    def template() -> dict[str, Any]:
        return {"_help": "Race/Sprint Race preparation. Event identity comes from --event YYYY-RR. Time values are seconds.", "session": "R", "race": {"_help": {"total_laps": "Normally resolved automatically from canonical event metadata. If unavailable, FastF1 session data may be used when available. Set an integer only to manually override the detected race distance."}, "total_laps": None}, "strategy": {"_help": {"max_stops": "Maximum stop count considered by unrestricted and rules-compliant optimiser runs.", "result_count": "Number of top strategy solutions retained and written per unrestricted or rules-compliant optimiser run. Fixed-stop cutoff envelopes retain only the single best solution per stop count."}, "max_stops": 3, "result_count": 10}, "tyre_prediction": {"_help": {"manual_override": "The current cached pre-race prediction is loaded first. Any non-null value below replaces only that value for this run; remaining values and uncertainty stay automatic."}, "manual_override": {compound: {"performance_delta_to_medium": None, "degradation_seconds_per_lap": None} for compound in ("SOFT", "MEDIUM", "HARD")}}, "pit_loss": {"_help": {"green": "Normal GREEN-state pit loss.", "sc_vsc": "Combined SC/VSC pit loss in V1.", "input_forms": "For each state provide either total, or both pit_in_s3 and pit_out_s1. Units are seconds. Split total equals S3 + S1; mixing both forms is rejected."}, "green": {"total": 20.5, "pit_in_s3": None, "pit_out_s1": None}, "sc_vsc": {"total": 11.3, "pit_in_s3": None, "pit_out_s1": None}}, "degradation_cutoff": {"_help": {"scan_min": "Minimum MEDIUM degradation in s/lap. Leave null for a prediction-centred automatic range that extends its lower bound toward zero when needed to find a 1→2 cutoff. An explicit value is a hard lower bound.", "scan_max": "Maximum MEDIUM degradation in s/lap; null selects automatic range.", "coarse_step": "Coarse scan step in s/lap; normally leave null.", "refine_tolerance": "Crossing refinement tolerance in s/lap; normally leave null."}, "scan_min": None, "scan_max": None, "coarse_step": None, "refine_tolerance": None}, "data_root": None, "report": {"language": "zh-CN", "template": "engineering"}}
