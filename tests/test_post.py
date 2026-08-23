from woweekend.workflows.post_race import compare_tyre_predictions, resolve_retro_pit_loss


def test_comparison_is_precomputed() -> None:
    before = {"compounds": {"MEDIUM": {"performance_delta_to_medium": 0.0, "degradation_seconds_per_lap": 0.1}}}
    after = {"compounds": {"MEDIUM": {"performance_delta_to_medium": 0.0, "degradation_seconds_per_lap": 0.12}}}
    row = compare_tyre_predictions(before, after)["compounds"][1]
    assert row["degradation"]["prediction_minus_retro"] == -0.01999999999999999


def test_pit_loss_uses_median_then_saved_fallback() -> None:
    output = resolve_retro_pit_loss(empirical={"normal": {"sample_count": 2, "total": 20.8, "pit_in_s3": 10.5, "pit_out_s1": 10.3}}, saved_pre_race_config={"pit_loss": {"green": {"pit_in_s3": 10, "pit_out_s1": 10}, "sc_vsc": {"pit_in_s3": 5.8, "pit_out_s1": 5.5}}})
    assert output["GREEN"]["source"] == "race_empirical_median"
    assert output["SC/VSC"]["source"] == "pre_race_fallback"
