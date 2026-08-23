import pytest

from woweekend.config.common import ConfigError
from woweekend.config.race import RaceConfig


def test_help_keys_are_ignored_and_defaults_resolve() -> None:
    raw = RaceConfig.template()
    raw["pit_loss"]["green"]["_units"] = "seconds"
    config = RaceConfig.parse(raw)
    assert config.report.language == "zh-CN"
    assert config.degradation_cutoff.scan_min is None


def test_unknown_normal_field_is_rejected_with_suggestion() -> None:
    raw = RaceConfig.template()
    raw["result_cout"] = 5
    with pytest.raises(ConfigError, match="result_count"):
        RaceConfig.parse(raw)


def test_generated_config_validates() -> None:
    RaceConfig.parse(RaceConfig.template())


def test_whole_pit_loss_is_supported_without_sector_split() -> None:
    config = RaceConfig.parse(RaceConfig.template())
    assert config.pit_loss.green.effective_total == 20.5
    assert config.pit_loss.green.pit_in_s3 is None


def test_whole_and_split_pit_loss_are_rejected_as_ambiguous() -> None:
    raw = RaceConfig.template()
    raw["pit_loss"]["green"] = {
        "total": 20.5, "pit_in_s3": 10.4, "pit_out_s1": 10.1,
    }
    with pytest.raises(ValueError, match="provide either total or both"):
        RaceConfig.parse(raw)
